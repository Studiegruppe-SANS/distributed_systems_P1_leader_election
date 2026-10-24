# implementering af den klassiske bully algoritme
# improved_bully_node.py arver herfra og overskriver de dele der optimeres

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


# beskeder der tæller med i election-message-complexity
ELECTION_MESSAGES = frozenset({MsgType.ELECTION, MsgType.OK, MsgType.COORDINATOR})


class State(Enum):
    NORMAL = "NORMAL"      # har en coordinator, eller er selv coordinator
    ELECTION = "ELECTION"  # har sendt ELECTION til højere noder, afventer OK
    WAITING = "WAITING"    # har fået OK, venter på COORDINATOR-besked


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
    ok: float = 3.0                  # ventetid på OK efter ELECTION
    coordinator: float = 5.0         # ventetid på COORDINATOR efter OK
    heartbeat_interval: float = 2.0  # tidsinterval mellem heartbeat-besker
    heartbeat_timeout: float = 5.0   # tid uden heartbeat, før der startes ny election
    # bruges kun for forbedret version: delay som sikrer, at en node når at vinde og sende COORDINATOR,
    # før en lavere node starter (anvender prioritetsforsinkelse fra Invitation-algoritmen, Garcia-Molina s. 53)
    election_delay_step: float = 4.0


@dataclass
class Stats:
    sent: Counter = field(default_factory=Counter)
    received: Counter = field(default_factory=Counter)
    elections_started: int = 0

    def election_sent(self) -> int:
        # antal sendte election-beskeder (ekskl. heartbeats)
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

    # kaldes udefra af app.py og tests
    @property
    def is_coordinator(self) -> bool:
        return self.coordinator_id == self.id

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
            # ingen højere node svarede OK, så denne node vinder
            self.log("ingen OK modtaget")
            self.declare_victory()

        elif self.state == State.WAITING and now >= self._deadline:
            # højere node svarede OK, men døde før COORDINATOR blev broadcastet
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

    # algoritmen for selve valget: start_election, declare_victory osv.
    def higher_peers(self) -> list[int]:
        return sorted(p for p in self.peers if p > self.id)

    def start_election(self) -> None:
        # starter ikke ny election, hvis der allerede kører en, eller der ventes på COORDINATOR
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

    # handlere som håndterer beskeder mellem noderne
    def _on_election(self, msg: Message) -> None:
        # svarer OK og starter eget valg, hvis lavere node har startet et valg
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
            # lavere node har udråbt sig selv som vinder, så der udskrives nyt valg
            self.log(f"lavere node {new} påstår at være coordinator -> election")
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

    # hjælper-funktioner
    def _send(self, target: int, type_: MsgType, **kwargs) -> None:
        msg = Message(type_, self.id, **kwargs)
        # tæller sendte beskeder op, også beskeder sendt til døde noder tæller med
        self.stats.sent[type_] += 1
        try:
            self._send_fn(target, msg)
        # hvis netværket fejler, skal det ikke crashe noden
        except Exception as e:
            self.log(f"kunne ikke sende {type_.value} til {target}: {e}")

    def log(self, text: str) -> None:
        self._log(f"Node {self.id} [{self.state.value}]: {text}")