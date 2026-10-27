# genererer compose.yaml med n bully-noder, så clusteret kan køres uden Kubernetes
# hver node kan tilgås via localhost:8080+id, se docker/README.md for kommandoer

import argparse
from pathlib import Path

OUT = Path(__file__).resolve().parent / "compose.yaml"
BASE_PORT = 8080

HEADER = """\
# genereret af gen_compose.py, så ret der, hvis noget skal rettes
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
      # i compose kan noderne tilgås direkte på bully-{{id}}, der er ingen headless service som i k8s
      PEER_URL_TEMPLATE: "http://{{service}}-{{id}}:{{port}}"
"""


def render(n: int) -> str:
    return HEADER.format(n=n) + "".join(
        SERVICE.format(id=i, n=n, port=BASE_PORT + i) for i in range(n))


def main() -> None:
    p = argparse.ArgumentParser(description="Genererer compose.yaml med n bully-noder")
    p.add_argument("--n", type=int, default=5, help="antal noder (default 5)")
    args = p.parse_args()
    OUT.write_text(render(args.n))
    print(f"Skrev {OUT.name} med {args.n} noder (porte {BASE_PORT}-{BASE_PORT + args.n - 1})")


if __name__ == "__main__":
    main()
