# forbedret bully-algoritme, arver fra BullyNode og overskriver de dele der optimeres

from __future__ import annotations

from typing import Optional

from bully_node import BullyNode, Message, MsgType, State


class ImprovedBullyNode(BullyNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._candidates: set[int] = set()  # højere noder der svarer OK
        self._election_at: Optional[float] = None  # forsinket start på election (prioritetsforsinkelse)

    def start_election(self) -> None:
        # samme guard som for BullyNode, sikrer at vi ikke fjerner kandidater fra igangværende election
        if self.state in (State.ELECTION, State.WAITING):
            return
        self._candidates.clear()
        self._election_at = None
        super().start_election()

    # noder der svarer OK gemmes som kandidater
    def _on_ok(self, msg: Message) -> None:
        if self.state in (State.ELECTION, State.WAITING):
            self._candidates.add(msg.sender_id)
            if self.state == State.ELECTION:
                self.state = State.WAITING

    def find_winner(self):
        winner = max(self._candidates)
        return winner

    def _election_delay(self) -> float:
        # prioritetsforsinkelse (Invitation-algoritmen, Garcia-Molina s. 53)
        # jo flere højere noder, jo længere ventetid. død coordinator tælles ikke med
        rivals = sum(1 for p in self.higher_peers() if p != self.coordinator_id)
        return rivals * self.t.election_delay_step

    # håndterer timeout i WAITING og forsinket valg, resten klares af BullyNode.tick()
    def tick(self) -> None:
        now = self.clock()

        if self.state == State.WAITING and now >= self._deadline:
            if self._candidates:
                # beder højeste kandidat om at blive coordinator
                # coordinator_id sættes først, når vinderens COORDINATOR kommer (Assertion 1)
                # udebliver denne, prøves den næste kandidat
                winner = self.find_winner()
                self._candidates.discard(winner)
                self._announce_coordinator(winner)
                self._deadline = now + self.t.coordinator
            else:
                # alle højere noder som har svaret er døde, derfor er denne node den højeste tilbage
                self.declare_victory()

        elif (self.state == State.NORMAL and not self.is_coordinator
              and now - self._last_heartbeat > self.t.heartbeat_timeout):
            if self._election_at is None:
                self._election_at = now + self._election_delay()
                self.log(f"coordinator timeout -> election om {self._election_at - now:.1f} s")
            if now >= self._election_at:
                self.start_election()

        else:
            # heartbeat, COORDINATOR eller ELECTION kom i mellemtiden, så forsinket valg annulleres
            self._election_at = None
            super().tick()

    def _on_coordinator(self, msg: Message) -> None:
        if msg.coordinator_id == self.id:
            if self.is_coordinator and self.state == State.NORMAL:
                # sender kun COORDINATOR til den der spurgte, ellers får den timeout og vælger næste kandidat
                self._send(msg.sender_id, MsgType.COORDINATOR, coordinator_id=self.id)
                return
            self.declare_victory()
        else:
            super()._on_coordinator(msg)

    # svarer OK uden at starte eget valg, nulstiller heartbeat så der ikke startes forsinket valg
    def _on_election(self, msg: Message) -> None:
        if msg.sender_id < self.id:
            self._send(msg.sender_id, MsgType.OK)
            self._last_heartbeat = self.clock()

    # COORDINATOR sendes til vinderen af valget, hvorefter vinderen broadcaster til alle
    def _announce_coordinator(self, winner: int) -> None:
        self._send(winner, MsgType.COORDINATOR, coordinator_id=winner)
