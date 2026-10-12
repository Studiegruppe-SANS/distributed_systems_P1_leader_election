from __future__ import annotations

from bully_node import BullyNode, Message, MsgType, State

class ImprovedBullyNode(BullyNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._candidates: list[int] = []  # resterende kandidater, hoejeste foerst


    def start_election(self) -> None:
        self._candidates.clear()
        super().start_election()

    # Replace completely: super() is never called
    def _on_ok(self, msg: Message) -> None:

        if self.state == State.ELECTION or self.state == State.WAITING:
            self._candidates.append(msg.sender_id)
            if self.state == State.ELECTION:
                self.state = State.WAITING
       
            
    def find_winner(self):
        winner = max(self._candidates)
        return winner

    # Partly replace: handle your case, pass everything else to the original
    def tick(self) -> None:
        now = self.clock()

        if self.state == State.WAITING and now >= self._deadline:  # your case
            winner = self.find_winner()
            self._announce_coordinator(winner)
            self.coordinator_id = winner
            self.state = State.NORMAL
            self._last_heartbeat = self.clock()
            self._deadline = None
        else:
            super().tick()

    def _on_coordinator(self, msg: Message) -> None:
        if msg.coordinator_id == self.id:
            if self.is_coordinator and self.state == State.NORMAL:
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
