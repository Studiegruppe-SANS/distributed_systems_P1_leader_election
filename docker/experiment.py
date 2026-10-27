# kører eksperimenterne i Kubernetes vha. kubectl, resultater gemmes som CSV
# election-tid måles fra første election starter til sidste skift af leder uden heartbeat timeout

# se docker/README.md for kommandoer og scenarier og project_report/experiments.md for metode

import argparse
import csv
import json
import random
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATEFULSET = HERE / "k8s" / "statefulset.yaml"
RESULTS = HERE.parent / "results"
HEARTBEAT_TIMEOUT = 5  # skal passe med Timeouts.heartbeat_timeout i bully_node.py
SETTLE = 2             # ekstra tid efter enighed, så de sidste beskeder når at lande
MAX_WAIT = 90          # maks. ventetid på enighed om ny leder


# kubectl hjælper-funktioner
def kubectl(*args, input=None, check=True):
    r = subprocess.run(["kubectl", *args], input=input, capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"kubectl {' '.join(args)} fejlede:\n{r.stderr.strip()}")
    return r


def http(pod, path, method="GET"):
    # kalder et endpoint inde i poden via kubectl exec, returnerer None ved error
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
    # indlæser algoritme, CLUSTER_SIZE og replicas fra den kørende StatefulSet
    data = json.loads(kubectl("get", "statefulset", "bully", "-o", "json").stdout)
    env = {e["name"]: e.get("value")
           for e in data["spec"]["template"]["spec"]["containers"][0].get("env", [])}
    return env.get("ALGORITHM", "classic"), int(env.get("CLUSTER_SIZE", "5")), data["spec"]["replicas"]


def on_all(path, method="GET"):
    # kalder endpoint på alle pods med et kubectl exec fra første pod, i stedet for et pr pod
    # pods der ikke svarer udelades
    pods = running_pods()
    if not pods:
        return []
    data = "None" if method == "GET" else "b''"
    code = (
        "import urllib.request as u, json\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "def call(p):\n"
        "    try:\n"
        f"        r=u.Request(f'http://{{p}}.bully:8080{path}', data={data}, method='{method}')\n"
        "        return json.loads(u.urlopen(r, timeout=3).read())\n"
        "    except Exception:\n"
        "        return None\n"
        f"pods={pods!r}\n"
        "with ThreadPoolExecutor(len(pods)) as ex:\n"
        "    print(json.dumps([r for r in ex.map(call, pods) if r]))\n"
    )
    r = kubectl("exec", pods[0], "--", "python", "-c", code, check=False)
    try:
        return json.loads(r.stdout) if r.returncode == 0 else []
    except json.JSONDecodeError:
        return []


def wait_ready(count, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        if len(running_pods()) == count and len(on_all("/status")) == count:
            return
        time.sleep(1)
    sys.exit(f"Ikke alle {count} pods blev klar inden for {timeout} s (tjek kubectl get pods)")


def wait_until_dead(pod, timeout=30):
    # venter til poden ikke længere svarer
    end = time.time() + timeout
    while time.time() < end:
        if http(pod, "/status") is None:
            return
        time.sleep(0.2)
    sys.exit(f"{pod} svarer stadig efter {timeout} s")


# kode til rapport
TYPES = ("ELECTION", "OK", "COORDINATOR")


def election_time(statuses):
    # sekunder fra første election starter til sidste lederskift blandt levende noder
    # None hvis ingen election er startet siden sidste /stats/reset
    alive = [s for s in statuses if not s.get("crashed")]
    starts = [s["first_election_at"] for s in alive if s.get("first_election_at")]
    if not starts:
        return None
    start = min(starts)
    ends = [s["coordinator_since"] for s in alive
            if s.get("coordinator_since") and s["coordinator_since"] >= start]
    return round(max(ends) - start, 3) if ends else None


def report(title, expected=None, note=None, expected_leader=None):
    # printer en tabel og returnerer en række til CSV
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
        state = "CRASHED" if s.get("crashed") else s["state"]
        print(f"{s['id']:>4} {state:>9} {str(s['coordinator']):>5} {s['elections_started']:>4} "
              f"{sent.get('ELECTION', 0):>8} {sent.get('OK', 0):>4} {sent.get('COORDINATOR', 0):>5} "
              f"{s['election_messages_sent']:>5}")
    total = sum(totals.values())
    print(f"{'sum':>4} {'':>9} {'':>5} {'':>4} "
          f"{totals['ELECTION']:>8} {totals['OK']:>4} {totals['COORDINATOR']:>5} {total:>5}")

    # nedbrudte noder har forældet leder, så disse tæller ikke med
    leaders = {s["coordinator"] for s in statuses if not s.get("crashed")}
    agreement = len(leaders) == 1
    leader = next(iter(leaders)) if agreement else None
    if agreement:
        print(f"Enighed: ja, alle levende noder peger på node {leader}")
    else:
        print(f"Enighed: NEJ, noderne peger på {sorted(leaders, key=str)}")
    if expected_leader is not None:
        print(f"Forventet leder: {expected_leader} -> {'OK' if leader == expected_leader else 'FORKERT'}")
    seconds = election_time(statuses)
    if seconds is not None:
        print(f"Election-tid: {seconds:.2f} s")
    if expected is not None:
        print(f"Teoretisk forventet: {expected} beskeder")
    if note:
        print(note)
    return {
        "algorithm": algo,
        "n": n,
        "expected_leader": expected_leader,
        "leader": leader,
        "agreement": agreement,
        "correct": agreement and (expected_leader is None or leader == expected_leader),
        "elections_started": sum(s["elections_started"] for s in statuses),
        **totals,
        "total": total,
        "expected_total": expected,
        "election_time_s": seconds,
    }


CSV_FIELDS = ["timestamp", "scenario", "run", "algorithm", "n", "expected_leader", "leader",
              "agreement", "correct", "elections_started", *TYPES, "total", "expected_total",
              "election_time_s"]


def write_row(path, row):
    # tilføjer en række til CSV-filen, header hvis filen er ny
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


# crash, recover og vent på ny leder
def crash(*ids):
    with ThreadPoolExecutor(len(ids)) as ex:
        list(ex.map(lambda i: http(f"bully-{i}", "/crash", "POST"), ids))
    print(f"Crashede node {', '.join(map(str, ids))}")


def recover(*ids):
    with ThreadPoolExecutor(len(ids)) as ex:
        list(ex.map(lambda i: http(f"bully-{i}", "/recover", "POST"), ids))
    print(f"Genoplivede node {', '.join(map(str, ids))}")


def wait_for_leader(leader, timeout=MAX_WAIT):
    # venter til alle aktive noder er i NORMAL og peger på samme leder
    # tilføjet ekstra par sekunder til forsinkede beskeder
    end = time.time() + timeout
    while time.time() < end:
        alive = [s for s in on_all("/status") if not s.get("crashed")]
        if alive and all(s["state"] == "NORMAL" and s["coordinator"] == leader for s in alive):
            time.sleep(SETTLE)
            return True
        time.sleep(0.5)
    print(f"ADVARSEL: ingen enighed om node {leader} efter {timeout} s")
    return False


# deploy af pods og de enkelte scenarier
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
    print("Alle pods kører.")


def ensure_full():
    # sikrer at CLUSTER_SIZE pods kører og har valgt leder
    _, n, replicas = config()
    if replicas != n or len(running_pods()) != n:
        print(f"Skalerer op til {n} pods ...")
        kubectl("scale", "statefulset", "bully", f"--replicas={n}")
        wait_ready(n)
        wait_for_leader(n - 1)
    if any(s.get("crashed") for s in on_all("/status")):
        print("Genopliver noder der stadig er crashet fra en tidligere kørsel ...")
        on_all("/recover", "POST")                # ingen effekt på noder der ikke er crashet
        wait_for_leader(n - 1)
    return n


def restore(n):
    print(f"\nGendanner {n} pods ...")
    kubectl("scale", "statefulset", "bully", f"--replicas={n}")
    wait_ready(n)
    wait_for_leader(n - 1)
    print("Klar til næste eksperiment.")


def cmd_startup(args):
    deploy(args.algorithm, args.n)
    _, n, _ = config()
    print("Venter på opstartsvalget ...")
    wait_for_leader(n - 1)
    return report("Opstart", expected_leader=n - 1)


def cmd_failover(args):
    n = ensure_full()
    leader = f"bully-{n - 1}"
    on_all("/stats/reset", "POST")
    print(f"Slår {leader} ihjel. Alle noder opdager det via heartbeat-timeout ...")
    kubectl("scale", "statefulset", "bully", f"--replicas={n - 1}")
    wait_until_dead(leader)
    wait_for_leader(n - 2)                        # venter på heartbeat timeout og derefter valget
    row = report("Failover, samtidig opdagelse", expected_leader=n - 2)
    restore(n)
    return row


def cmd_worst(args):
    n = ensure_full()
    algo, _, _ = config()
    leader = f"bully-{n - 1}"
    on_all("/stats/reset", "POST")

    # node 0 overvåger lederen inde fra poden, så valget startes med det samme, før andre
    # noder opdager crashet
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
    # sikrer mindst 1 svar fra lederen, derefter slås lederen ihjel
    # sørger for valget ikke bliver startet for tidligt
    first = watcher.stdout.readline().strip()
    if first != "alive":
        rest, err = watcher.communicate(timeout=10)
        sys.exit(f"Node 0 kunne ikke nå {leader} før eksperimentet: {first} {rest} {err}".strip())

    print(f"Slår {leader} ihjel og lader node 0 starte valget med det samme ...")
    kubectl("scale", "statefulset", "bully", f"--replicas={n - 1}")
    out, err = watcher.communicate(timeout=90)
    if "triggered" not in out:
        sys.exit(f"Node 0 nåede ikke at starte valget:\n{err.strip()}")
    wait_for_leader(n - 2)

    expected = n * (n - 1) if algo == "classic" else 3 * n - 3
    note = None
    if algo == "improved":
        note = ("Tjek kolonnen 'valg': kun node 0 bør have startet et valg. Har flere noder startet et, "
                "er det ikke længere ren værste-tilfælde. Kør i så fald igen.")
    row = report("Værste tilfælde, kun node 0 starter", expected, note, expected_leader=n - 2)
    restore(n)
    return row


def cmd_crash_middle(args):
    # en vilkårlig node der ikke er coordinator dør, dermed burde der ikke være valg
    n = ensure_full()
    victim = random.randrange(n - 1)
    on_all("/stats/reset", "POST")
    crash(victim)
    print(f"Venter {HEARTBEAT_TIMEOUT + SETTLE} s (længere end heartbeat-timeout) ...")
    time.sleep(HEARTBEAT_TIMEOUT + SETTLE)
    row = report(f"Crash af ikke-leder (node {victim})", expected=0, expected_leader=n - 1)
    recover(victim)
    wait_for_leader(n - 1)
    return row


def cmd_crash_two(args):
    # 2 højeste noder dør på samme tid, så tredjehøjeste node skal være coordinator
    n = ensure_full()
    if n < 3:
        sys.exit("crash-two kræver mindst 3 noder")
    on_all("/stats/reset", "POST")
    crash(n - 1, n - 2)
    wait_for_leader(n - 3)
    row = report(f"Crash af node {n - 1} og {n - 2} samtidig", expected_leader=n - 3)
    recover(n - 2, n - 1)
    wait_for_leader(n - 1)
    return row


def cmd_recover_leader(args):
    # coordinator dør, ny vælges, og gamle kommer tilbage og starter valg
    # kun beskederne fra recovery måles
    n = ensure_full()
    crash(n - 1)
    wait_for_leader(n - 2)
    on_all("/stats/reset", "POST")
    recover(n - 1)
    wait_for_leader(n - 1)
    return report(f"Node {n - 1} kommer tilbage", expected_leader=n - 1)


SCENARIOS = {
    "startup": cmd_startup,
    "failover": cmd_failover,
    "worst": cmd_worst,
    "crash-middle": cmd_crash_middle,
    "crash-two": cmd_crash_two,
    "recover-leader": cmd_recover_leader,
}


def run_scenario(name, args, out):
    # kører et scenarie, og skriver evt. en CSV række pr. kørsel
    rows = []
    for run in range(1, args.runs + 1):
        if args.runs > 1:
            print(f"\n##### {name}: kørsel {run}/{args.runs} #####")
        row = SCENARIOS[name](args)
        row = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"), "scenario": name, "run": run, **row}
        rows.append(row)
        if out:
            write_row(out, row)
    return rows


def cmd_sweep(args):
    # kører alle scenarier for begge algoritmer og alle n
    out = args.out or RESULTS / f"sweep-{time.strftime('%Y%m%d-%H%M%S')}.csv"
    for algo in args.algorithms:
        for n in args.ns:
            deploy(algo, n)
            wait_for_leader(n - 1)
            for name in args.scenarios:
                run_scenario(name, args, out)
    print(f"\nFærdig. Resultater i {out}")


def main():
    p = argparse.ArgumentParser(description="Eksperimenter mod Bully-poddene i Kubernetes")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runs", type=int, default=1, help="gentag scenariet R gange")
    common.add_argument("--out", type=Path, help="CSV-fil der tilføjes en række pr. kørsel")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("reset")
    for name in ("deploy", *SCENARIOS):
        sp = sub.add_parser(name, parents=[] if name == "deploy" else [common])
        if name in ("deploy", "startup"):
            sp.add_argument("--algorithm", choices=["classic", "improved"])
            sp.add_argument("--n", type=int)
    sp = sub.add_parser("sweep", parents=[common])
    sp.add_argument("--algorithms", nargs="+", default=["classic", "improved"],
                    choices=["classic", "improved"])
    sp.add_argument("--ns", nargs="+", type=int, default=[5, 10, 15, 20])
    sp.add_argument("--scenarios", nargs="+", default=list(SCENARIOS), choices=list(SCENARIOS))
    args = p.parse_args()

    if args.cmd == "status":
        report("Status")
    elif args.cmd == "reset":
        print(f"Nulstillede {len(on_all('/stats/reset', 'POST'))} pods")
    elif args.cmd == "deploy":
        deploy(args.algorithm, args.n)
    elif args.cmd == "sweep":
        # startup skal genbruge den algoritme og det n, som sweep netop har brugt
        args.algorithm = args.n = None
        cmd_sweep(args)
    else:
        run_scenario(args.cmd, args, args.out)


if __name__ == "__main__":
    main()
