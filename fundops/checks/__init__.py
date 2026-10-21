"""Exception engine. Each check module contributes exceptions and "passes".

A pass is recorded as deliberately as a failure: the list of things that *did*
tie is what lets a CFO trust the things that didn't.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Exception_, SEVERITY_ORDER
from .statement_checks import run_statement_checks
from .capital_checks import run_capital_checks
from .correspondence_checks import run_correspondence_checks
from .prior_close_checks import run_prior_close_checks


@dataclass
class CheckRun:
    exceptions: list[Exception_] = field(default_factory=list)
    passes: list[dict] = field(default_factory=list)   # {check, subject, detail, evidence}


def rank(exceptions: list[Exception_]) -> list[Exception_]:
    """Severity first; within severity, decision-required first, then $ at stake."""
    ordered = sorted(exceptions, key=lambda e: (SEVERITY_ORDER[e.severity], not e.decision_required, -abs(e.amount_impact_usd)))
    for i, e in enumerate(ordered, start=1):
        e.rank = i
    return ordered


def run_all(ctx) -> CheckRun:
    run = CheckRun()
    for fn in (run_prior_close_checks, run_statement_checks, run_capital_checks, run_correspondence_checks):
        exc, passes = fn(ctx)
        run.exceptions.extend(exc)
        run.passes.extend(passes)
    run.exceptions = rank(run.exceptions)
    return run
