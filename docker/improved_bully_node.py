from __future__ import annotations

from typing import Optional

from bully_node import BullyNode, Message, MsgType, State

class ImprovedBullyNode(BullyNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._candidates: set[int] = set()  # higher nodes that answered OK
        self._election_at: Optional[float] = None  # delayed election start (priority delay)


    def start_election(self) -> None:
        # Same guard as in BullyNode: don't wipe candidates of an ongoing election
        if self.state in (State.ELECTION, State.WAITING):
            return
        self._candidates.clear()
        self._election_at = None
        super().start_election()

    # Replace completely: super() is never called
    def _on_ok(self, msg: Message) -> None:
        if self.state in (State.ELECTION, State.WAITING):
            self._candidates.add(msg.sender_id)
            if self.state == State.ELECTION:
                self.state = State.WAITING


    def find_winner(self):
        winner = max(self._candidates)
        return winner

    def _election_delay(self) -> float:
        # Priority delay (Garcia-Molina p. 53): the more nodes that could win
        # before me, the longer I wait. The highest live node starts first, and
        # its COORDINATOR reaches the lower nodes before their delay runs out.
        # The current coordinator is not counted, since it is the one that failed.
        rivals = sum(1 for p in self.higher_peers() if p != self.coordinator_id)
        return rivals * self.t.election_delay_step

    # Partly replace: handle your case, pass everything else to the original
    def tick(self) -> None:
        now = self.clock()

        if self.state == State.WAITING and now >= self._deadline:
            if self._candidates:
                # Ask the highest candidate to take over. We do NOT set
                # coordinator_id here: we stay in WAITING until the winner's own
                # COORDINATOR broadcast arrives (Assertion 1). If it never comes,
                # the winner is dead and we try the next candidate.
                winner = self.find_winner()
                self._candidates.discard(winner)
                self._announce_coordinator(winner)
                self._deadline = now + self.t.coordinator
            else:
                # Every higher node that answered has died -> I am the highest left
                self.declare_victory()

        elif (self.state == State.NORMAL and not self.is_coordinator
              and now - self._last_heartbeat > self.t.heartbeat_timeout):
            if self._election_at is None:
                self._election_at = now + self._election_delay()
                self.log(f"coordinator timeout -> election om {self._election_at - now:.1f} s")
            if now >= self._election_at:
                self.start_election()

        else:
            # A heartbeat, COORDINATOR or ELECTION arrived meanwhile -> cancel the delayed start
            self._election_at = None
            super().tick()

    def _on_coordinator(self, msg: Message) -> None:
        if msg.coordinator_id == self.id:
            if self.is_coordinator and self.state == State.NORMAL:
                # Already coordinator: confirm to the asker only, otherwise it
                # times out in WAITING and picks the next candidate
                self._send(msg.sender_id, MsgType.COORDINATOR, coordinator_id=self.id)
                return
            self.declare_victory()
        else:
            super()._on_coordinator(msg)


    def _on_election(self, msg: Message) -> None:
        if msg.sender_id < self.id:
            self._send(msg.sender_id, MsgType.OK)
            self._last_heartbeat = self.clock()

    # New method that doesn't exist in BullyNode
    def _announce_coordinator(self, winner: int) -> None:
        self._send(winner, MsgType.COORDINATOR, coordinator_id=winner)
