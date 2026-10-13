#!/usr/bin/env python3
"""
experiment.py — koerer eksperimenter mod Bully-poddene i Kubernetes og samler beskedtal.

Koerer paa jeres Mac (kun Pythons standardbibliotek) og styrer alt via kubectl.
Beskedtallene hentes fra /status i hver pod med `kubectl exec` og laegges sammen.

Brug (fra docker-mappen):
  python3 experiment.py status                         vis tilstand og beskedtal for alle pods
  python3 experiment.py reset                          nulstil beskedtal i alle pods
  python3 experiment.py deploy --algorithm classic     start poddene forfra med en given algoritme
  python3 experiment.py startup                        maal opstartsvalget (starter poddene forfra)
  python3 experiment.py failover                       lederen doer, alle opdager det samtidig
  python3 experiment.py worst                          lederen doer, kun node 0 starter valget

deploy og startup tager ogsaa --n (antal noder). De aendrer IKKE statefulset.yaml paa disken,
de sender en tilpasset kopi til Kubernetes.
"""

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

STATEFULSET = Path(__file__).resolve().parent / "k8s" / "statefulset.yaml"
STARTUP_DELAY = 3      # skal passe med STARTUP_DELAY i app.py
SETTLE = 10            # sekunder vi venter paa, at et valg er helt overstaaet


# ----------------------------------------------------------------------
# kubectl-hjaelpere
# ----------------------------------------------------------------------
def kubectl(*args, input=None, check=True):
    r = subprocess.run(["kubectl", *args], input=input, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"kubectl {' '.join(args)} fejlede:\n{r.stderr.strip()}")
    return r


def http(pod, path, method="GET"):
    """Kalder et endpoint inde i poden (localhost:8080) via kubectl exec. None ved fejl."""
    data = "None" if method == "GET" else "b''"
    code = ("import urllib.request as u;"
            f"r=u.Request('http://localhost:8080{path}', data={data}, method='{method}');"
            "print(u.urlopen(r, timeout=3).read().decode())")
    r = kubectl("exec", pod, "--", "python", "-c", code, check=False)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def running_pods():
    data = json.loads(kubectl("get", "pods", "-l", "app=bully", "-o", "json").stdout)
    names = [p["metadata"]["name"] for p in data["items"]
             if p["status"].get("phase") == "Running" and not p["metadata"].get("deletionTimestamp")]
    return sorted(names, key=lambda name: int(name.rsplit("-", 1)[1]))


def config():
    """Laeser algoritme, CLUSTER_SIZE og replicas fra den koerende StatefulSet."""
    data = json.loads(kubectl("get", "statefulset", "bully", "-o", "json").stdout)
    env = {e["name"]: e.get("value")
           for e in data["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    return env.get("ALGORITHM", "classic"), int(env.get("CLUSTER_SIZE", "5")), data["spec"]["replicas"]


def on_all(path, method="GET"):
    pods = running_pods()
    with ThreadPoolExecutor(max(len(pods), 1)) as ex:
        return [r for r in ex.map(lambda p: http(p, path, method), pods) if r]


def wait_ready(count, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if len(running_pods()) == count and len(on_all("/status")) == count:
            return
        time.sleep(1)
    sys.exit(f"Ikke alle {count} pods blev klar inden for {timeout} s (tjek kubectl get pods)")


def wait_until_dead(pod, timeout=30):
    """Venter til poden ikke laengere svarer (den lukker ned ved SIGTERM)."""
    end = time.time() + timeout
    while time.time() < end:
        if http(pod, "/status") is None:
            return
        time.sleep(0.2)
    sys.exit(f"{pod} svarer stadig efter {timeout} s")


# ----------------------------------------------------------------------
# Rapport
# ----------------------------------------------------------------------
TYPES = ("ELECTION", "OK", "COORDINATOR")


def report(title, expected=None, note=None):
    algo, n, _ = config()
    statuses = on_all("/status")
    print(f"\n=== {title} | algoritme: {algo} | CLUSTER_SIZE={n} | pods der svarer: {len(statuses)} ===")
    print(f"{'node':>4} {'tilstand':>9} {'leder':>5} {'valg':>4} "
          f"{'ELECTION':>8} {'OK':>4} {'COORD':>5} {'i alt':>5}")
    totals = dict.fromkeys(TYPES, 0)
    for s in sorted(statuses, key=lambda s: s["id"]):
        sent = s["sent"]
        for t in TYPES:
            totals[t] += sent.get(t, 0)
        print(f"{s['id']:>4} {s['state']:>9} {str(s['coordinator']):>5} {s['elections_started']:>4} "
              f"{sent.get('ELECTION', 0):>8} {sent.get('OK', 0):>4} {sent.get('COORDINATOR', 0):>5} "
              f"{s['election_messages_sent']:>5}")
    total = sum(totals.values())
    print(f"{'sum':>4} {'':>9} {'':>5} {'':>4} "
          f"{totals['ELECTION']:>8} {totals['OK']:>4} {totals['COORDINATOR']:>5} {total:>5}")
    leaders = {s["coordinator"] for s in statuses}
    if len(leaders) == 1:
        print(f"Enighed: ja, alle peger paa node {next(iter(leaders))}")
    else:
        print(f"Enighed: NEJ, noderne peger paa {sorted(leaders, key=str)}")
    if expected is not None:
        print(f"Teoretisk forventet: {expected} beskeder")
    if note:
        print(note)
    return total


# ----------------------------------------------------------------------
# Kommandoer
# ----------------------------------------------------------------------
def deploy(algorithm=None, n=None):
    cur_algo, cur_n = "classic", 5
    try:
        cur_algo, cur_n, _ = config()
    except SystemExit:
        pass                                    # ingen StatefulSet endnu
    algorithm, n = algorithm or cur_algo, n or cur_n

    text = STATEFULSET.read_text()
    text = re.sub(r"(replicas:\s*)\d+", rf"\g<1>{n}", text, count=1)
    text = re.sub(r'(name:\s*CLUSTER_SIZE\s*\n\s*value:\s*)"[^"]*"', rf'\g<1>"{n}"', text)
    text, found = re.subn(r'(name:\s*ALGORITHM\s*\n\s*value:\s*)"[^"]*"', rf'\g<1>"{algorithm}"', text)
    if not found:
        sys.exit("Fandt ikke ALGORITHM i k8s/statefulset.yaml")

    print(f"Starter {n} pods forfra med algoritmen '{algorithm}' ...")
    kubectl("delete", "statefulset", "bully", "--ignore-not-found", "--wait=true")
    kubectl("wait", "--for=delete", "pod", "-l", "app=bully", "--timeout=60s", check=False)
    kubectl("apply", "-f", "-", input=text)
    wait_ready(n)
    print("Alle pods koerer.")


def ensure_full():
    """Sikrer at alle CLUSTER_SIZE pods koerer og har valgt en leder."""
    _, n, replicas = config()
    if replicas != n or len(running_pods()) != n:
        print(f"Skalerer op til {n} pods ...")
        kubectl("scale", "statefulset", "bully", f"--replicas={n}")
        wait_ready(n)
        time.sleep(STARTUP_DELAY + SETTLE)
    return n


def restore(n):
    print(f"\nGendanner {n} pods ...")
    kubectl("scale", "statefulset", "bully", f"--replicas={n}")
    wait_ready(n)
    time.sleep(STARTUP_DELAY + SETTLE)
    print("Klar til naeste eksperiment.")


def cmd_startup(args):
    deploy(args.algorithm, args.n)
    print(f"Venter {STARTUP_DELAY + SETTLE} s paa opstartsvalget ...")
    time.sleep(STARTUP_DELAY + SETTLE)
    report("Opstart")


def cmd_failover(args):
    n = ensure_full()
    leader = f"bully-{n - 1}"
    on_all("/stats/reset", "POST")
    print(f"Slaar {leader} ihjel. Alle noder opdager det via heartbeat-timeout ...")
    kubectl("scale", "statefulset", "bully", f"--replicas={n - 1}")
    wait_until_dead(leader)
    time.sleep(5 + SETTLE)                        # heartbeat_timeout + valget
    report("Failover, samtidig opdagelse")
    restore(n)


def cmd_worst(args):
    n = ensure_full()
    algo, _, _ = config()
    leader = f"bully-{n - 1}"
    on_all("/stats/reset", "POST")

    # Node 0 overvaager selv lederen inde fra klyngen og starter valget i samme
    # oejeblik, lederen holder op med at svare. Det sker inde i poden, saa
    # kubectl-forsinkelser ikke naar at lade de andre noder ramme deres
    # heartbeat-timeout foer node 0's ELECTION kommer frem.
    watcher_code = (
        "import urllib.request as u, time, sys\n"
        f"t='http://{leader}.bully:8080/status'\n"
        "alive=False\n"
        "end=time.time()+90\n"
        "while time.time()<end:\n"
        "    try:\n"
        "        u.urlopen(t, timeout=1).read()\n"
        "        if not alive:\n"
        "            alive=True\n"
        "            print('alive', flush=True)\n"
        "    except Exception as e:\n"
        "        if alive:\n"
        "            break\n"
        "        err=repr(e)\n"
        "    time.sleep(0.05)\n"
        "if not alive:\n"
        "    print('never-alive', err, flush=True)\n"
        "    sys.exit(1)\n"
        "u.urlopen(u.Request('http://localhost:8080/election', data=b'', method='POST'), timeout=3).read()\n"
        "print('triggered', flush=True)\n"
    )
    watcher = subprocess.Popen(["kubectl", "exec", "bully-0", "--", "python", "-c", watcher_code],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # Vent til overvaageren har set lederen svare mindst én gang. Foerst derefter
    # slaar vi lederen ihjel, saa valget ikke kan blive startet for tidligt.
    first = watcher.stdout.readline().strip()
    if first != "alive":
        rest, err = watcher.communicate(timeout=10)
        sys.exit(f"Node 0 kunne ikke naa {leader} foer eksperimentet: {first} {rest} {err}".strip())

    print(f"Slaar {leader} ihjel og lader node 0 starte valget med det samme ...")
    kubectl("scale", "statefulset", "bully", f"--replicas={n - 1}")
    out, err = watcher.communicate(timeout=90)
    if "triggered" not in out:
        sys.exit(f"Node 0 naaede ikke at starte valget:\n{err.strip()}")
    time.sleep(SETTLE)

    expected = n * (n - 1) if algo == "classic" else 3 * n - 3
    note = None
    if algo == "improved":
        note = ("Tjek kolonnen 'valg': kun node 0 boer have startet et valg. Har flere noder startet et, "
                "er det ikke laengere ren vaerste-tilfaelde. Koer i saa fald igen.")
    report("Vaerste tilfaelde, kun node 0 starter", expected, note)
    restore(n)


def main():
    p = argparse.ArgumentParser(description="Eksperimenter mod Bully-poddene i Kubernetes")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("reset")
    for name in ("deploy", "startup"):
        sp = sub.add_parser(name)
        sp.add_argument("--algorithm", choices=["classic", "improved"])
        sp.add_argument("--n", type=int)
    sub.add_parser("failover")
    sub.add_parser("worst")
    args = p.parse_args()

    if args.cmd == "status":
        report("Status")
    elif args.cmd == "reset":
        print(f"Nulstillede {len(on_all('/stats/reset', 'POST'))} pods")
    elif args.cmd == "deploy":
        deploy(args.algorithm, args.n)
    elif args.cmd == "startup":
        cmd_startup(args)
    elif args.cmd == "failover":
        cmd_failover(args)
    elif args.cmd == "worst":
        cmd_worst(args)


if __name__ == "__main__":
    main()