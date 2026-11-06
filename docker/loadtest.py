# load test af fortune cookie hjemmesiden: sender mange HTTP-requests hurtigt efter hinanden
# og måler svartider. Kun standardbiblioteket, så det kan køres direkte på Mac'en
#
#   python3 docker/loadtest.py                                  # 2000 requests, 20 samtidige
#   python3 docker/loadtest.py --requests 10000 --concurrency 50
#
# rå målinger -> results/loadtest-<tid>.csv, opsummering -> results/loadtest-summary.csv

import argparse
import csv
import http.client
import json
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

RESULTS = Path(__file__).resolve().parent.parent / "results"


class Client:
    # én keep-alive forbindelse pr. tråd, genopbygges hvis den fejler
    def __init__(self, url: str, timeout: float):
        parts = urlsplit(url)
        self.host, self.port = parts.hostname, parts.port or 80
        self.path = parts.path or "/"
        self.timeout = timeout
        self.conn = None

    def get(self):
        if self.conn is None:
            self.conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            self.conn.request("GET", self.path)
            resp = self.conn.getresponse()
            body = resp.read()
            return resp.status, body
        except Exception:
            self.conn.close()
            self.conn = None
            raise


def percentile(sorted_values, p):
    if not sorted_values:
        return None
    k = (len(sorted_values) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (k - lo)


def run(url, total, concurrency, timeout):
    local = threading.local()
    counter = iter(range(total))
    lock = threading.Lock()
    rows = []
    t0 = time.perf_counter()

    def worker():
        local.client = Client(url, timeout)
        while True:
            with lock:
                i = next(counter, None)
            if i is None:
                return
            start = time.perf_counter()
            status, served_by, error = None, None, ""
            try:
                status, body = local.client.get()
                if status == 200:
                    served_by = json.loads(body).get("served_by")
            except Exception as e:
                error = type(e).__name__
            ms = (time.perf_counter() - start) * 1000
            with lock:
                rows.append({"i": i, "t_s": round(start - t0, 4), "latency_ms": round(ms, 3),
                             "status": status, "served_by": served_by, "error": error})

    with ThreadPoolExecutor(concurrency) as ex:
        for _ in range(concurrency):
            ex.submit(worker)
    wall = time.perf_counter() - t0
    rows.sort(key=lambda r: r["i"])
    return rows, wall


def summarize(rows, wall, args):
    ok = sorted(r["latency_ms"] for r in rows if r["status"] == 200)
    fails = len(rows) - len(ok)
    pick = lambda p: round(percentile(ok, p), 2) if ok else None
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "url": args.url,
        "requests": len(rows),
        "concurrency": args.concurrency,
        "ok": len(ok),
        "failed": fails,
        "wall_s": round(wall, 3),
        "throughput_rps": round(len(rows) / wall, 1) if wall else None,
        "min_ms": round(ok[0], 2) if ok else None,
        "mean_ms": round(statistics.mean(ok), 2) if ok else None,
        "median_ms": pick(50),
        "p90_ms": pick(90),
        "p95_ms": pick(95),
        "p99_ms": pick(99),
        "max_ms": round(ok[-1], 2) if ok else None,
        "served_by": ",".join(sorted({str(r["served_by"]) for r in rows if r["served_by"] is not None})),
    }


def write_csv(path, rows, fields, append=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists() or not append
    with path.open("a" if append else "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def main():
    p = argparse.ArgumentParser(description="Load test af fortune cookie hjemmesiden")
    p.add_argument("--url", default="http://localhost:30080/api/cookie")
    p.add_argument("--requests", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--timeout", type=float, default=5.0)
    p.add_argument("--warmup", type=int, default=20, help="requests før målingen, tæller ikke med")
    args = p.parse_args()

    if args.warmup:
        run(args.url, args.warmup, min(args.concurrency, args.warmup), args.timeout)

    print(f"Sender {args.requests} requests til {args.url} med {args.concurrency} samtidige ...")
    rows, wall = run(args.url, args.requests, args.concurrency, args.timeout)
    s = summarize(rows, wall, args)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    raw = RESULTS / f"loadtest-{stamp}.csv"
    write_csv(raw, rows, ["i", "t_s", "latency_ms", "status", "served_by", "error"])
    write_csv(RESULTS / "loadtest-summary.csv", [s], list(s), append=True)

    print(f"\nOK: {s['ok']}  fejl: {s['failed']}  tid: {s['wall_s']} s  throughput: {s['throughput_rps']} req/s")
    print(f"svartid (ms)  min {s['min_ms']}  mean {s['mean_ms']}  median {s['median_ms']}  "
          f"p90 {s['p90_ms']}  p95 {s['p95_ms']}  p99 {s['p99_ms']}  max {s['max_ms']}")
    print(f"besvaret af node(r): {s['served_by'] or '-'}")
    print(f"rå data: {raw}")


if __name__ == "__main__":
    main()
