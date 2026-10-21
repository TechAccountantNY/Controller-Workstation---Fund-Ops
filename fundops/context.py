"""Everything the checks and the roll-forward need, assembled once by ``pipeline.ingest``."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import Assumptions, DEFAULT_ASSUMPTIONS, PERIOD
from .ingest.reference import Reference
from .models import CapitalInstruction, ManagerStatement, Message, WireCredit


@dataclass
class MonthContext:
    period: str
    ref: Reference
    messages: list[Message]
    statements: list[ManagerStatement]          # all parsed, any period
    statement_failures: list[dict]
    instructions: list[CapitalInstruction]
    wires: list[WireCredit]
    capital_failures: list[dict]
    assumptions: Assumptions = field(default_factory=lambda: DEFAULT_ASSUMPTIONS)

    # ---- convenience -------------------------------------------------------
    @property
    def sleeves(self) -> list[str]:
        return list(self.ref.sleeves["sleeve"])

    @property
    def vehicles(self) -> list[str]:
        return list(self.ref.vehicles["vehicle"])

    def statement_for(self, sleeve: str, period: Optional[str] = None) -> Optional[ManagerStatement]:
        period = period or self.period
        for s in self.statements:
            if s.sleeve == sleeve and s.period == period:
                return s
        return None

    def messages_in(self, *categories: str) -> list[Message]:
        return [m for m in self.messages if m.category in categories]

    def message(self, msg_id: str) -> Optional[Message]:
        return next((m for m in self.messages if m.msg_id == msg_id), None)
