"""
Unit-tests af BullyNode (classic) og ImprovedBullyNode (improved).

Noderne testes uden netvaerk: send og clock injiceres, saa testen kan se hvilke
beskeder en node sender, og selv bestemme hvad klokken er.

Koer fra repo-roden:
  python3 -m pytest testing/test_bully_node.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docker"))

from bully_node import BullyNode, Message, MsgType, State, Timeouts  # noqa: E402
from improved_bully_node import ImprovedBullyNode                     # noqa: E402

ELECTION, OK, COORDINATOR = MsgType.ELECTION, MsgType.OK, MsgType.COORDINATOR
T = Timeouts()


def make(cls, node_id, n=5):
    """Node med falsk netvaerk (liste af sendte beskeder) og falsk ur."""
    sent, clock = [], [0.0]
    node = cls(node_id, range(n), send=lambda target, m: sent.append((target, m.type)),
               clock=lambda: clock[0])
    return node, sent, clock


# ----------------------------------------------------------------------
# Classic
# ----------------------------------------------------------------------
def test_classic_election_only_to_higher_nodes():
    node, sent, _ = make(BullyNode, 2)
    node.start_election()
    assert sent == [(3, ELECTION), (4, ELECTION)]


def test_classic_wins_if_no_ok_before_timeout():
    node, _, clock = make(BullyNode, 2)
    node.start_election()
    clock[0] = T.ok
    node.tick()
    assert node.is_coordinator


def test_classic_answers_lower_node_and_starts_own_election():
    node, sent, _ = make(BullyNode, 2)
    node.on_message(Message(ELECTION, 0))
    assert sent == [(0, OK), (3, ELECTION), (4, ELECTION)]


def test_classic_heartbeat_timeout_starts_election():
    node, sent, clock = make(BullyNode, 2)
    node.on_message(Message(COORDINATOR, 4, 4))
    clock[0] = T.heartbeat_timeout + 0.1
    node.tick()
    assert node.state == State.ELECTION


# ----------------------------------------------------------------------
# Improved
# ----------------------------------------------------------------------
def test_improved_answers_lower_node_without_new_election():
    node, sent, _ = make(ImprovedBullyNode, 2)
    node.on_message(Message(ELECTION, 0))
    assert sent == [(0, OK)]                       # classic sender ogsaa 2 x ELECTION


def test_improved_asks_only_highest_candidate():
    node, sent, clock = make(ImprovedBullyNode, 0)
    node.start_election()
    for sender in (1, 2, 3):                       # node 4 er doed og svarer ikke
        node.on_message(Message(OK, sender))
    sent.clear()
    clock[0] = T.ok
    node.tick()
    assert sent == [(3, COORDINATOR)]


def test_improved_priority_delay():
    node, sent, clock = make(ImprovedBullyNode, 1)
    node.on_message(Message(COORDINATOR, 4, 4))
    clock[0] = T.heartbeat_timeout + 0.1
    node.tick()
    assert sent == []                              # venter: node 2 og 3 faar forspring
    clock[0] += 2 * T.election_delay_step
    node.tick()
    assert sent == [(2, ELECTION), (3, ELECTION), (4, ELECTION)]


# ----------------------------------------------------------------------
# Hele valget med 5 noder i hukommelsen: lederen (4) er doed, kun node 0 starter
# ----------------------------------------------------------------------
def worst_case(cls, n=5):
    clock, queue = [0.0], []
    nodes = [cls(i, range(n), send=lambda t, m: queue.append((t, m)), clock=lambda: clock[0])
             for i in range(n)]

    def deliver():
        while queue:
            target, m = queue.pop(0)
            if target != n - 1:                    # beskeder til den doede leder forsvinder
                nodes[target].on_message(m)

    nodes[n - 1].start()                           # node 4 bliver leder ...
    deliver()
    for node in nodes:
        node.stats.reset()
    nodes[0].start_election()                      # ... og doer. Kun node 0 opdager det.
    deliver()
    while clock[0] < T.ok + T.coordinator:
        clock[0] += 0.1
        for node in nodes[:-1]:
            node.tick()
        deliver()
    leaders = {node.coordinator_id for node in nodes[:-1]}
    messages = sum(node.stats.election_sent() for node in nodes)
    return leaders, messages


@pytest.mark.parametrize("cls, expected", [(BullyNode, 5 * 4), (ImprovedBullyNode, 3 * 5 - 3)])
def test_worst_case(cls, expected):
    leaders, messages = worst_case(cls)
    assert leaders == {3}                          # alle er enige om den hoejeste levende node
    assert messages == expected                    # classic n(n-1), improved 3n-3
