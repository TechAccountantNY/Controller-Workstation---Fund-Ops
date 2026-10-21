"""Ingest → check → roll forward. One function, deterministic, no side effects
except the files it is asked to write."""
from __future__ import annotations

from dataclasses import dataclass

from .checks import CheckRun, run_all
from .config import Assumptions, DEFAULT_ASSUMPTIONS, PERIOD
from .context import MonthContext
from .ingest import classify_messages, load_inbox, load_reference, parse_capital_activity, parse_statements
from .rollforward import RollForward, build as build_rollforward


@dataclass
class RunResult:
    ctx: MonthContext
    checks: CheckRun
    roll: RollForward


def ingest(assumptions: Assumptions = DEFAULT_ASSUMPTIONS, period: str = PERIOD) -> MonthContext:
    ref = load_reference()
    messages = classify_messages(load_inbox())
    statements, st_fail = parse_statements(messages)
    instructions, wires, cap_fail = parse_capital_activity(messages, list(ref.vehicles.vehicle), list(ref.investors.investor))
    return MonthContext(period=period, ref=ref, messages=messages, statements=statements, statement_failures=st_fail,
                        instructions=instructions, wires=wires, capital_failures=cap_fail, assumptions=assumptions)


def run(assumptions: Assumptions = DEFAULT_ASSUMPTIONS) -> RunResult:
    ctx = ingest(assumptions)
    checks = run_all(ctx)
    roll = build_rollforward(ctx)
    return RunResult(ctx=ctx, checks=checks, roll=roll)
