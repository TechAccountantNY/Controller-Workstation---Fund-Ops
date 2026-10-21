"""Normalised records. Every parsed figure carries provenance (which document,
which extraction method, how confident) so an exception can cite its evidence."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any, Optional


@dataclass
class Message:
    msg_id: str                 # e.g. "01"
    filename: str
    sender: str
    sender_domain: str
    to: str
    sent: date
    subject: str
    attachment: Optional[str]   # filename or directory as named in the header
    body: str
    category: str = "unclassified"
    counterparty: Optional[str] = None   # manager / investor / vendor name when known
    relevance: str = "unknown"           # "in_scope" | "informational" | "noise"
    classification_rule: str = ""

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["sent"] = self.sent.isoformat()
        d["body"] = self.body.strip()
        return d


@dataclass
class ManagerStatement:
    sleeve: str
    period: str
    opening_nav: float
    return_pct: float               # normalised to percent (1.42 means 1.42%)
    closing_nav: float
    management_fee: Optional[float]
    incentive_fee: Optional[float]
    source_doc: str
    received: date
    extraction_method: str          # csv | xlsx | email_body | fixed_width_text
    confidence: str                 # high | medium | low
    return_raw: str = ""            # as it appeared in the document
    return_unit_inferred: str = ""  # percent | decimal | percent_string
    notes: str = ""                 # manager's own notes / caveats verbatim
    status_label: str = ""          # e.g. "final", "estimate (final)", "system generated"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["received"] = self.received.isoformat()
        d.pop("raw")
        return d


@dataclass
class CapitalInstruction:
    instr_id: str
    kind: str                      # subscription | redemption
    investor: str
    vehicle: str
    amount: float
    dealing_date: date
    notice_date: date              # date the instruction was received
    source_doc: str
    signed_document: Optional[str] # attachment filename when a signed form was supplied
    acknowledged_by_ta: bool = False
    ta_ack_doc: Optional[str] = None
    notes: str = ""

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["dealing_date"] = self.dealing_date.isoformat()
        d["notice_date"] = self.notice_date.isoformat()
        return d


@dataclass
class WireCredit:
    wire_id: str
    account: str
    vehicle: str
    amount: float
    value_date: date
    originator: str
    reference: str
    source_doc: str

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["value_date"] = self.value_date.isoformat()
        return d


@dataclass
class Exception_:
    """An exception for a human. ``key`` is stable across runs so decisions survive re-ingestion."""
    key: str
    rank: int
    severity: str                  # critical | high | medium | low | info
    category: str                  # intra_document | cross_document | absence | process
    area: str                      # statements | capital | correspondence | prior_period | data_quality
    title: str
    detail: str
    amount_impact_usd: float       # best estimate of $ at stake (0 when not quantifiable)
    evidence: list[str]            # document references
    recommended_action: str
    decision_required: bool
    counterparty: str = ""
    findings: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d["evidence"] = " | ".join(self.evidence)
        d["findings"] = " | ".join(self.findings)
        return d


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
