#!/usr/bin/env python3
"""
gen_compose.py — genererer compose.yaml med N Bully-noder (bully-0 ... bully-(N-1)).

Docker Compose er den hurtige vej til at koere klyngen uden Kubernetes: ingen cluster,
og hver node kan naas direkte fra Mac'en paa localhost:8080+id.

Brug (fra docker-mappen):
  python3 gen_compose.py --n 5                       skriv compose.yaml med 5 noder
  ALGORITHM=improved docker compose up --build -d    start klyngen (classic er default)
  curl localhost:8084/status                         se node 4
  curl -X POST localhost:8084/crash                  simuler at node 4 doer
  docker compose down                                stop det hele
"""

import argparse
from pathlib import Path

OUT = Path(__file__).resolve().parent / "compose.yaml"
BASE_PORT = 8080

HEADER = """\
# GENERERET af gen_compose.py — ret dér i stedet for her.
#   python3 gen_compose.py --n {n}
name: bully

x-node: &node
  build: .
  image: bully-app:latest
  stop_grace_period: 2s
  healthcheck:
    test: ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://localhost:8080/status', timeout=2)"]
    interval: 2s
    timeout: 3s
    retries: 15

services:
"""

SERVICE = """\
  bully-{id}:
    <<: *node
    hostname: bully-{id}
    ports: ["{port}:8080"]
    environment:
      NODE_ID: "{id}"
      CLUSTER_SIZE: "{n}"
      ALGORITHM: ${{ALGORITHM:-classic}}
      SERVICE_NAME: bully
      # Compose-DNS: service-navnet bully-{{id}} er nok (ingen headless service som i k8s)
      PEER_URL_TEMPLATE: "http://{{service}}-{{id}}:{{port}}"
"""


def render(n: int) -> str:
    return HEADER.format(n=n) + "".join(
        SERVICE.format(id=i, n=n, port=BASE_PORT + i) for i in range(n))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--n", type=int, default=5, help="antal noder (default 5)")
    args = p.parse_args()
    OUT.write_text(render(args.n))
    print(f"Skrev {OUT.name} med {args.n} noder (porte {BASE_PORT}-{BASE_PORT + args.n - 1})")


if __name__ == "__main__":
    main()
