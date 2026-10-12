from _future_ import annotations

from bully_node import BullyNode, Message, MsgType, State

class ImprovedBullyNode(BullyNode):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ok_from: set[int] = set()   # hoejere noder der har svaret OK
        self._candidates: list[int] = []  # resterende kandidater, hoejeste foerst