"""
bully_node.py — Bully Election (Garcia-Molina 1982) som ren logik.

Klassen kender intet til netvaerk, traade eller sockets. Den har tre indgange:
    start()           kaldes ved opstart (starter en election)
    on_message(msg)   kaldes af runtime naar en besked ankommer
    tick()            kaldes periodisk (fx hvert 100 ms) og haandterer timeouts
og én udgang, som injiceres udefra:
    send(target_id, msg)   UDP, HTTP (Kubernetes) eller et fake netvaerk i tests

Uret (clock) injiceres ogsaa, saa tests kan styre tiden deterministisk.

Den forbedrede version laves som en subklasse, der overskriver
start_election() og/eller handlerne _on_election / _on_ok / _on_coordinator.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Optional


class MsgType(str, Enum):
    ELECTION = "ELECTION"
    OK = "OK"
    COORDINATOR = "COORDINATOR"
    HEARTBEAT = "HEARTBEAT"


# Beskeder der tæller med i election-message-complexity.
# HEARTBEAT er failure detection og holdes adskilt, ellers forurener den maalingerne.
ELECTION_MESSAGES = frozenset({MsgType.ELECTION, MsgType.OK, MsgType.COORDINATOR})


class State(Enum):
    NORMAL = "NORMAL"      # kender en coordinator (eller er selv coordinator)
    ELECTION = "ELECTION"  # har sendt ELECTION til hoejere noder, venter paa OK
    WAITING = "WAITING"    # har faaet OK, venter paa COORDINATOR


@dataclass
class Message:
    type: MsgType
    sender_id: int
    coordinator_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "sender_id": self.sender_id,
            "coordinator_id": self.coordinator_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(MsgType(d["type"]), int(d["sender_id"]), d.get("coordinator_id"))


@dataclass
class Timeouts:
    ok: float = 3.0                  # ventetid paa OK efter ELECTION (T hos Garcia-Molina)
    coordinator: float = 5.0         # ventetid paa COORDINATOR efter OK
    heartbeat_interval: float = 2.0  # coordinator sender heartbeat saa ofte
    heartbeat_timeout: float = 5.0   # uden heartbeat saa laenge -> ny election
    # Kun forbedret version: ekstra ventetid pr. hoejere kandidat foer election
    # (prioritetsforsinkelse, Garcia-Molina s. 53). Skal vaere > ok, saa en hoejere
    # node naar at vinde og sende COORDINATOR, foer en lavere node starter.
    election_delay_step: float = 4.0


@dataclass
class Stats:
    sent: Counter = field(default_factory=Counter)
    received: Counter = field(default_factory=Counter)
    elections_started: int = 0

    def election_sent(self) -> int:
        """Antal sendte election-beskeder (ekskl. heartbeats)."""
        return sum(n for t, n in self.sent.items() if t in ELECTION_MESSAGES)

    def reset(self) -> None:
        self.sent.clear()
        self.received.clear()
        self.elections_started = 0


SendFn = Callable[[int, Message], None]


class BullyNode:
    def __init__(
        self,
        node_id: int,
        peer_ids: Iterable[int],
        send: SendFn,
        clock: Callable[[], float] = time.monotonic,
        timeouts: Optional[Timeouts] = None,
        log: Optional[Callable[[str], None]] = None,
    ):
        self.id = node_id
        self.peers: set[int] = set(peer_ids) - {node_id}
        self._send_fn = send
        self.clock = clock
        self.t = timeouts or Timeouts()
        self._log = log or (lambda m: None)

        self.state = State.NORMAL
        self.coordinator_id: Optional[int] = None
        self.stats = Stats()

        self._deadline: Optional[float] = None  # timeout for ELECTION/WAITING
        self._last_heartbeat = clock()
        self._next_heartbeat = clock()

    # ------------------------------------------------------------------
    # Offentlig API
    # ------------------------------------------------------------------
    @property
    def is_coordinator(self) -> bool:
        return self.coordinator_id == self.id

    def set_peers(self, peer_ids: Iterable[int]) -> None:
        """Opdater kendte noder (fx fra Kubernetes headless service discovery)."""
        self.peers = set(peer_ids) - {self.id}

    def start(self) -> None:
        self.start_election()

    def on_message(self, msg: Message) -> None:
        self.stats.received[msg.type] += 1
        self.log(f"modtog {msg.type.value} fra {msg.sender_id}")
        handler = {
            MsgType.ELECTION: self._on_election,
            MsgType.OK: self._on_ok,
            MsgType.COORDINATOR: self._on_coordinator,
            MsgType.HEARTBEAT: self._on_heartbeat,
        }[msg.type]
        handler(msg)

    def tick(self) -> None:
        now = self.clock()

        if self.state == State.ELECTION and now >= self._deadline:
            # Ingen hoejere node svarede OK -> jeg vinder
            self.log("ingen OK modtaget")
            self.declare_victory()

        elif self.state == State.WAITING and now >= self._deadline:
            # En hoejere node svarede OK, men doede foer den sendte COORDINATOR
            self.log("ingen COORDINATOR modtaget -> genstarter election")
            self.state = State.NORMAL
            self.start_election()

        elif self.state == State.NORMAL:
            if self.is_coordinator:
                if now >= self._next_heartbeat:
                    for p in sorted(self.peers):
                        self._send(p, MsgType.HEARTBEAT)
                    self._next_heartbeat = now + self.t.heartbeat_interval
            elif now - self._last_heartbeat > self.t.heartbeat_timeout:
                self.log("coordinator timeout -> starter election")
                self.start_election()

    # ------------------------------------------------------------------
    # Algoritmen (overskriv disse i den forbedrede version)
    # ------------------------------------------------------------------
    def higher_peers(self) -> list[int]:
        return sorted(p for p in self.peers if p > self.id)

    def start_election(self) -> None:
        # Koerer der allerede en election (eller venter vi paa en hoejere
        # nodes COORDINATOR), starter vi ikke en ny.
        if self.state in (State.ELECTION, State.WAITING):
            return

        self.stats.elections_started += 1
        self.coordinator_id = None
        self.log("*** starter election ***")

        higher = self.higher_peers()
        if not higher:
            self.declare_victory()
            return

        self.state = State.ELECTION
        self._deadline = self.clock() + self.t.ok
        for p in higher:
            self._send(p, MsgType.ELECTION)

    def declare_victory(self) -> None:
        self.state = State.NORMAL
        self.coordinator_id = self.id
        self._deadline = None
        self.log("*** jeg er coordinator ***")
        for p in sorted(self.peers):
            self._send(p, MsgType.COORDINATOR, coordinator_id=self.id)
        self._next_heartbeat = self.clock() + self.t.heartbeat_interval

    # ------------------------------------------------------------------
    # Handlere
    # ------------------------------------------------------------------
    def _on_election(self, msg: Message) -> None:
        if msg.sender_id < self.id:
            self._send(msg.sender_id, MsgType.OK)
            self.start_election()

    def _on_ok(self, msg: Message) -> None:
        if self.state == State.ELECTION:
            self.state = State.WAITING
            self._deadline = self.clock() + self.t.coordinator

    def _on_coordinator(self, msg: Message) -> None:
        new = msg.coordinator_id if msg.coordinator_id is not None else msg.sender_id
        if new < self.id:
            # En lavere node har udråbt sig selv -> "bully" den med en ny election
            self.log(f"lavere node {new} paastaar at vaere coordinator -> election")
            self.state = State.NORMAL
            self.start_election()
            return
        self.state = State.NORMAL
        self.coordinator_id = new
        self._deadline = None
        self._last_heartbeat = self.clock()
        self.log(f"ny coordinator: {new}")

    def _on_heartbeat(self, msg: Message) -> None:
        if msg.sender_id == self.coordinator_id:
            self._last_heartbeat = self.clock()

    # ------------------------------------------------------------------
    # Hjaelpere
    # ------------------------------------------------------------------
    def _send(self, target: int, type_: MsgType, **kwargs) -> None:
        msg = Message(type_, self.id, **kwargs)
        # Taelles som sendt uanset om modtageren lever — i Bully koster en
        # besked til en doed node stadig en besked.
        self.stats.sent[type_] += 1
        try:
            self._send_fn(target, msg)
        except Exception as e:  # netvaerksfejl maa aldrig vaelte algoritmen
            self.log(f"kunne ikke sende {type_.value} til {target}: {e}")

    def log(self, text: str) -> None:
        self._log(f"Node {self.id} [{self.state.value}]: {text}")