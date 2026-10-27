# systemtest af begge algoritmer i rigtige containere (Docker Compose, 5 noder)
# hver algoritme får sit eget cluster, og testene kører i rækkefølge på samme cluster
# kræver at Docker kører: python3 -m pytest testing/test_system.py -v

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"
N = 5
LEADER = N - 1


# hjælper-funktioner
# kalder endpoint på en node via localhost, node i har port 8080 + i
def call(node, path, method="GET"):
    req = urllib.request.Request(f"http://localhost:{8080 + node}{path}",
                                 data=None if method == "GET" else b"", method=method)
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read())


def statuses():
    return [call(i, "/status") for i in range(N)]


def alive():
    return [s for s in statuses() if not s["crashed"]]


def wait_until(predicate, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if predicate():
                return
        except OSError:
            pass                                  # noden er ikke oppe endnu
        time.sleep(0.5)
    pytest.fail(f"Timeout efter {timeout} s: {what}. Status: {statuses()}")


def agree_on(leader):
    nodes = alive()
    return all(s["state"] == "NORMAL" and s["coordinator"] == leader for s in nodes)


def wait_for_leader(leader, timeout=45):
    wait_until(lambda: agree_on(leader), timeout, f"alle levende noder er enige om node {leader}")


def reset_stats():
    for i in range(N):
        call(i, "/stats/reset", "POST")


def total_election_messages():
    return sum(s["election_messages_sent"] for s in statuses())


def compose(*args, algorithm):
    env = {**os.environ, "ALGORITHM": algorithm}
    subprocess.run(["docker", "compose", *args], cwd=DOCKER_DIR, env=env, check=True)


# et cluster pr. algoritme
@pytest.fixture(scope="module", params=["classic", "improved"])
def cluster(request):
    algorithm = request.param
    # sikrer at compose.yaml har N noder
    subprocess.run(["python3", "gen_compose.py", "--n", str(N)], cwd=DOCKER_DIR, check=True)
    compose("up", "--build", "-d", "--wait", "--force-recreate", algorithm=algorithm)
    try:
        assert all(s["algorithm"] == algorithm for s in statuses())
        yield algorithm
    finally:
        compose("down", algorithm=algorithm)


# tests, køres i denne rækkefølge for hver algoritme
def test_startup_elects_highest(cluster):
    wait_for_leader(LEADER)


def test_leader_crash_elects_next_highest(cluster):
    wait_for_leader(LEADER)
    call(LEADER, "/crash", "POST")
    wait_for_leader(LEADER - 1)
    call(LEADER, "/recover", "POST")
    wait_for_leader(LEADER)                       # den gamle leder starter valg og bliver coordinator


def test_two_highest_crash(cluster):
    wait_for_leader(LEADER)
    call(LEADER, "/crash", "POST")
    call(LEADER - 1, "/crash", "POST")
    wait_for_leader(LEADER - 2, timeout=60)
    call(LEADER - 1, "/recover", "POST")
    call(LEADER, "/recover", "POST")
    wait_for_leader(LEADER)


def test_non_leader_crash_keeps_leader(cluster):
    wait_for_leader(LEADER)
    reset_stats()
    call(2, "/crash", "POST")
    time.sleep(8)                                 # længere end heartbeat-timeout (5 s)
    assert agree_on(LEADER)
    assert sum(s["elections_started"] for s in alive()) == 0
    call(2, "/recover", "POST")
    wait_for_leader(LEADER)


def test_worst_case_message_complexity(cluster):
    # lederen dør, og kun node 0 starter valget. beskedtallet skal matche teorien:
    # classic n(n-1), improved 3(n-1)
    wait_for_leader(LEADER)
    time.sleep(1)                                 # venter på at sene beskeder lander
    reset_stats()
    call(LEADER, "/crash", "POST")
    call(0, "/election", "POST")
    wait_for_leader(LEADER - 1)
    time.sleep(1)                                 # venter på at sene beskeder lander
    expected = N * (N - 1) if cluster == "classic" else 3 * N - 3
    assert total_election_messages() == expected
    call(LEADER, "/recover", "POST")
    wait_for_leader(LEADER)
