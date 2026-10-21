"""Command line.

  python -m fundops run              ingest, check, roll forward, write outputs/, refresh state
  python -m fundops seed             load the Controller's recorded dispositions into the decision log
  python -m fundops reset            clear the decision log (re-walk the month live)
  python -m fundops decide KEY DISPOSITION --by "Name" --why "reason"
  python -m fundops serve [--port N] run the workstation UI
  python -m fundops status           one-screen summary
"""
from __future__ import annotations

import argparse
import sys

from .arrivals import build_arrivals
from .config import Assumptions, OUTPUT_DIR, SEED_DECISIONS, STATE_DB
from .pipeline import run as run_pipeline
from .report import write_outputs
from . import state as st


def cmd_run(args) -> int:
    assumptions = Assumptions(kestrel_redemption_scenario=args.scenario)
    result = run_pipeline(assumptions)
    arrivals = build_arrivals(result.ctx)
    con = st.connect(STATE_DB)
    run_id = st.record_run(con, result, arrivals)
    decisions = st.decision_log(con)
    files = write_outputs(result, arrivals, decisions if len(decisions) else None, OUTPUT_DIR)
    ex = result.checks.exceptions
    print(f"run #{run_id}: {len(result.ctx.messages)} messages, {len(result.ctx.statements)} statements parsed, "
          f"{len(result.ctx.statement_failures)} parse failures, {len(ex)} exceptions "
          f"({sum(e.severity == 'critical' for e in ex)} critical / {sum(e.severity == 'high' for e in ex)} high), "
          f"{len(result.checks.passes)} checks passed")
    for e in ex:
        print(f"  {e.rank:>2} [{e.severity:8s}] {e.title[:100]}")
    print(f"wrote {len(files)} files to {OUTPUT_DIR}")
    return 0


def cmd_seed(args) -> int:
    con = st.connect(STATE_DB)
    if con.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 0:
        print("no exceptions in state — run `python -m fundops run` first"); return 1
    n = st.seed_decisions(con, SEED_DECISIONS)
    print(f"recorded {n} dispositions from {SEED_DECISIONS.name}")
    _refresh_outputs(con)
    return 0


def _refresh_outputs(con) -> None:
    """Re-render outputs/ so the Markdown and decision_log.csv reflect the decision log."""
    result = run_pipeline(Assumptions())
    arrivals = build_arrivals(result.ctx)
    decisions = st.decision_log(con)
    write_outputs(result, arrivals, decisions if len(decisions) else None, OUTPUT_DIR)
    print("outputs/ refreshed")


def cmd_reset(args) -> int:
    con = st.connect(STATE_DB)
    st.reset_decisions(con)
    print("decision log cleared; all exceptions open")
    _refresh_outputs(con)
    return 0


def cmd_decide(args) -> int:
    con = st.connect(STATE_DB)
    i = st.record_decision(con, args.key, args.disposition, args.by, args.why)
    print(f"decision #{i} recorded: {args.disposition.upper()} on {args.key}")
    _refresh_outputs(con)
    return 0


def cmd_status(args) -> int:
    con = st.connect(STATE_DB)
    df = st.exceptions_with_decisions(con)
    if df.empty:
        print("no state — run `python -m fundops run`"); return 1
    open_ = df[df.disposition == ""]
    print(f"{len(df)} exceptions; {len(df) - len(open_)} dispositioned, {len(open_)} awaiting a decision")
    for r in df.itertuples():
        print(f"  {r.rank:>2} [{r.severity:8s}] {(r.disposition or '· open'):13s} {r.title[:90]}")
    return 0


def cmd_serve(args) -> int:
    from .app import create_app
    app = create_app()
    print(f"Controller Workstation — Fund Ops: http://127.0.0.1:{args.port}")
    app.run(host="127.0.0.1", port=args.port, debug=False)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="fundops", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("--scenario", choices=("deferred", "as_queued"), default="deferred",
                                               help="Kestrel Point redemption base case"); r.set_defaults(fn=cmd_run)
    sub.add_parser("seed").set_defaults(fn=cmd_seed)
    sub.add_parser("reset").set_defaults(fn=cmd_reset)
    d = sub.add_parser("decide"); d.add_argument("key"); d.add_argument("disposition", choices=[x.lower() for x in st.DISPOSITIONS] + list(st.DISPOSITIONS))
    d.add_argument("--by", required=True); d.add_argument("--why", required=True); d.set_defaults(fn=cmd_decide)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    s = sub.add_parser("serve"); s.add_argument("--port", type=int, default=8765); s.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
