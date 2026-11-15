# failover-eksperiment for fortune cookie hjemmesiden:
# en "bruger" henter cookies hvert 50 ms, mens leder-poden slukkes. Måler hvor længe der går,
# før en ny leder er valgt, og hvor længe brugeren ikke kan få cookies
#
#   python3 docker/failover_test.py                 # kubectl delete pod <leder>, StatefulSet genstarter den
#   python3 docker/failover_test.py --mode scale    # skalerer ned, så lederen IKKE kommer tilbage
#
# rå målinger -> results/failover-<tid>.csv, opsummering -> results/failover-summary.csv

import argparse
import csv
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# genbruger kubectl-hjælperne fra experiment.py
from experiment import (config, election_time, kubectl, on_all,  # noqa: E402
                        wait_for_leader, wait_ready)

RESULTS = HERE.parent / "results" / "kubernetes" / "failover"


def get_cookie(url, timeout):
    # returnerer (status, served_by, latency_ms), status None hvis der slet ikke kom svar
    start = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read())
            return start, r.status, data.get("served_by"), (time.time() - start) * 1000
    except urllib.error.HTTPError as e:
        return start, e.code, None, (time.time() - start) * 1000
    except Exception:
        return start, None, None, (time.time() - start) * 1000


class Poller(threading.Thread):
    # simulerer en bruger der trykker på knappen hele tiden
    def __init__(self, url, interval, timeout):
        super().__init__(daemon=True)
        self.url, self.interval, self.timeout = url, interval, timeout
        self.samples = []
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.is_set():
            self.samples.append(get_cookie(self.url, self.timeout))
            self.stop_event.wait(self.interval)

    def stop(self):
        self.stop_event.set()
        self.join()


def outages(samples, after):
    # sammenhængende perioder med fejlede requests efter tidspunktet `after`
    windows, start = [], None
    last_ok = None
    for t, status, served_by, _ in samples:
        ok = status == 200
        if t < after:
            if ok:
                last_ok = t
            continue
        if not ok and start is None:
            start = t
        elif ok and start is not None:
            windows.append({"first_fail": start, "first_ok": t, "served_by_after": served_by,
                            "last_ok_before": last_ok})
            start = None
        if ok:
            last_ok = t
    if start is not None:
        windows.append({"first_fail": start, "first_ok": None, "served_by_after": None,
                        "last_ok_before": last_ok})
    return windows


def leader_changes(samples):
    # hvilken node der besvarede requests over tid
    changes, current = [], object()
    for t, status, served_by, _ in samples:
        if status == 200 and served_by != current:
            changes.append((t, served_by))
            current = served_by
    return changes


def write_csv(path, rows, fields, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or not append
    with path.open("a" if append else "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser(description="Sluk leder-poden og mål failover")
    p.add_argument("--url", default="http://localhost:30080/api/cookie")
    p.add_argument("--mode", choices=["delete", "scale"], default="delete",
                   help="delete: kubectl delete pod (kommer tilbage), scale: skaler ned (kommer ikke tilbage)")
    p.add_argument("--interval", type=float, default=0.05, help="sekunder mellem brugerens requests")
    p.add_argument("--timeout", type=float, default=1.0, help="timeout pr. request")
    p.add_argument("--baseline", type=float, default=3.0, help="sekunder med normal drift før nedbrud")
    p.add_argument("--observe", type=float, default=40.0, help="sekunder der måles efter nedbrud")
    args = p.parse_args()

    _, n, _ = config()
    _, status, leader, _ = get_cookie(args.url, args.timeout)
    if status != 200:
        sys.exit(f"Hjemmesiden svarer ikke ({status}) på {args.url}. Kører web-servicen, og er der en leder?")
    if args.mode == "scale" and leader != n - 1:
        sys.exit(f"--mode scale fjerner bully-{n - 1}, men lederen er bully-{leader}")
    pod = f"bully-{leader}"

    on_all("/stats/reset", "POST")
    poller = Poller(args.url, args.interval, args.timeout)
    poller.start()
    print(f"Leder er {pod}. Måler normal drift i {args.baseline} s ...")
    time.sleep(args.baseline)

    kill_at = time.time()
    if args.mode == "delete":
        print(f"Sletter {pod} (kubectl delete pod) ...")
        kubectl("delete", "pod", pod, "--wait=false")
    else:
        print(f"Skalerer StatefulSet ned til {n - 1}, så {pod} forsvinder ...")
        kubectl("scale", "statefulset", "bully", f"--replicas={n - 1}")

    print(f"Måler i {args.observe} s efter nedbruddet ...")
    time.sleep(args.observe)
    poller.stop()

    statuses = on_all("/status")
    alive = [s for s in statuses if not s.get("crashed")]
    leaders = {s["coordinator"] for s in alive}
    starts = [s["first_election_at"] for s in alive if s.get("first_election_at")]
    first_election = min(starts) if starts else None
    elect_s = election_time(statuses)

    samples = poller.samples
    windows = outages(samples, kill_at)
    changes = leader_changes(samples)
    after = [s for s in samples if s[0] >= kill_at]
    failed = sum(1 for s in after if s[1] != 200)
    ok_lat = [s[3] for s in after if s[1] == 200]

    rel = lambda t: f"{t - kill_at:+.2f} s" if t is not None else "-"
    print(f"\n=== Failover ({args.mode}) af {pod} | CLUSTER_SIZE={n} ===")
    print(f"requests efter nedbrud: {len(after)}, fejlede: {failed}")
    for i, w in enumerate(windows, 1):
        dur = (w["first_ok"] - w["last_ok_before"]) if w["first_ok"] and w["last_ok_before"] else None
        print(f"udfald {i}: første fejl {rel(w['first_fail'])}, første svar igen {rel(w['first_ok'])} "
              f"fra node {w['served_by_after']}, brugeren uden cookies i "
              f"{f'{dur:.2f} s' if dur is not None else '-'}")
    print("hvem svarede (tid ift. nedbrud):", ", ".join(f"{rel(t)} -> node {sb}" for t, sb in changes))
    print(f"første ELECTION startet: {rel(first_election)}  (opdagelse via heartbeat-timeout)")
    print(f"election-tid (første ELECTION -> sidste lederskift): {elect_s if elect_s is not None else '-'} s")
    print(f"enighed nu: {'ja, node ' + str(next(iter(leaders))) if len(leaders) == 1 else sorted(leaders, key=str)}")
    if ok_lat:
        print(f"max svartid for gennemførte requests efter nedbrud: {max(ok_lat):.1f} ms")
    print("(tider fra pods og Mac sammenlignes, så små urforskelle kan påvirke tallene)")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = RESULTS / f"failover-{stamp}.csv"
    write_csv(raw, [{"t_rel_s": round(t - kill_at, 4), "status": st, "served_by": sb,
                     "latency_ms": round(ms, 2)} for t, st, sb, ms in samples],
              ["t_rel_s", "status", "served_by", "latency_ms"])
    main_window = windows[0] if windows else None
    summary = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mode": args.mode,
        "n": n,
        "old_leader": leader,
        "new_leader": next(iter(leaders)) if len(leaders) == 1 else None,
        "first_fail_s": round(main_window["first_fail"] - kill_at, 3) if main_window else None,
        "first_ok_s": (round(main_window["first_ok"] - kill_at, 3)
                       if main_window and main_window["first_ok"] else None),
        "first_election_s": round(first_election - kill_at, 3) if first_election else None,
        "election_time_s": elect_s,
        "outages": len(windows),
        "failed_requests": failed,
        "requests_after_kill": len(after),
    }
    write_csv(RESULTS / "summary.csv", [summary], list(summary), append=True)
    print(f"rå data: {raw}")

    if args.mode == "scale":
        print(f"\nGendanner {n} pods ...")
        kubectl("scale", "statefulset", "bully", f"--replicas={n}")
        wait_ready(n)
        wait_for_leader(n - 1)
    print("Færdig.")


if __name__ == "__main__":
    main()
