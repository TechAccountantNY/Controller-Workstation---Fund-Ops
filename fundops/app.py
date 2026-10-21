"""Controller Workstation — Fund Ops: the live view.

A small Flask app over the SQLite state and the (deterministic, cheap)
pipeline. Tabs mirror the Controller Workstation pattern: what arrived, what
the numbers are, what's wrong, and a place for a person to decide — with the
decision recorded, attributed and reasoned.

No JavaScript framework, no build step: ``python -m fundops serve``.
"""
from __future__ import annotations

import json
from datetime import date

from flask import Flask, redirect, render_template_string, request, url_for

from .arrivals import build_arrivals
from .config import AS_OF, Assumptions, PERIOD, STATE_DB
from .pipeline import run as run_pipeline
from . import state as st

BASE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Controller Workstation — Fund Ops</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1b1f24;--muted:#5f6b7a;--line:#e3e7ec;--accent:#1f4e79;--crit:#b3261e;--high:#c7641c;--med:#9a7b00;--low:#2e6b3a;--info:#4a5a6a}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
header{background:var(--accent);color:#fff;padding:12px 24px;display:flex;justify-content:space-between;align-items:baseline}
header h1{font-size:17px;margin:0;font-weight:600}header small{opacity:.85}
nav{background:var(--panel);border-bottom:1px solid var(--line);padding:0 24px;display:flex;gap:4px;flex-wrap:wrap}
nav a{display:block;padding:10px 12px;color:var(--muted);text-decoration:none;border-bottom:3px solid transparent}
nav a.on{color:var(--accent);border-color:var(--accent);font-weight:600}
main{padding:20px 24px;max-width:1500px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card .k{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}.card .v{font-size:22px;font-weight:600;margin-top:2px}
.card .s{font-size:12px;color:var(--muted)}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;margin-bottom:18px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{background:#eef1f5;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em;color:var(--muted)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr:last-child td{border-bottom:0}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;color:#fff;text-transform:uppercase}
.critical{background:var(--crit)}.high{background:var(--high)}.medium{background:var(--med)}.low{background:var(--low)}.info{background:var(--info)}
.st-ok{color:var(--low);font-weight:600}.st-bad{color:var(--crit);font-weight:600}.st-warn{color:var(--high);font-weight:600}
.muted{color:var(--muted)}.small{font-size:12px}h2{font-size:16px;margin:18px 0 8px}h3{font-size:14px;margin:14px 0 6px}
details{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 14px;margin-bottom:10px}
summary{cursor:pointer;font-weight:600}
form.decide{display:grid;grid-template-columns:160px 1fr;gap:8px;margin-top:10px;padding-top:10px;border-top:1px dashed var(--line)}
form.decide textarea,form.decide input,form.decide select{width:100%;padding:6px;border:1px solid var(--line);border-radius:6px;font:inherit}
form.decide button{grid-column:2;justify-self:start;background:var(--accent);color:#fff;border:0;padding:8px 14px;border-radius:6px;cursor:pointer}
.dec{background:#f0f6ee;border-left:4px solid var(--low);padding:8px 10px;margin-top:8px;border-radius:4px}
.ev code{background:#eef1f5;padding:1px 5px;border-radius:4px;font-size:12px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:18px}@media(max-width:1000px){.row{grid-template-columns:1fr}}
.banner{background:#fff8e6;border:1px solid #f0d88a;border-radius:8px;padding:10px 14px;margin-bottom:16px}
.actions a{display:inline-block;margin-right:10px;color:var(--accent)}
pre{white-space:pre-wrap;background:#f3f5f7;padding:8px;border-radius:6px;font-size:12px}
</style></head><body>
<header><h1>Controller Workstation — Fund Ops module <small>· Brindlewood complex · {{period}} · as of {{as_of}}</small></h1><small>{{n_open}} exceptions awaiting a decision</small></header>
<nav>{% for key,label in tabs %}<a href="{{url_for('page', tab=key)}}" class="{{'on' if key==tab else ''}}">{{label}}</a>{% endfor %}</nav>
<main>{{ body|safe }}</main></body></html>"""

TABS = [("overview", "Overview"), ("inbox", "Inbox"), ("arrivals", "Arrivals"), ("rollforward", "Roll-forward"),
        ("capital", "Capital activity"), ("exceptions", "Exceptions"), ("decisions", "Decision log"), ("assumptions", "Assumptions")]


def _m(x) -> str:
    try:
        if x is None or x != x:
            return "—"
        return f"{float(x):,.0f}"
    except (TypeError, ValueError):
        return str(x)


def _pct(x) -> str:
    try:
        return "—" if x is None or x != x else f"{float(x):.2f}%"
    except (TypeError, ValueError):
        return str(x)


def _status_class(s: str) -> str:
    s = s.upper()
    if s.startswith("RECEIVED — MATCHES") or s == "RECEIVED" or s.startswith("PROCESSED"):
        return "st-ok"
    if "OUTSTANDING" in s or "OVERDUE" in s or "DOES NOT MATCH" in s or "UNPARSEABLE" in s or s.startswith("HELD"):
        return "st-bad"
    return "st-warn"


def create_app() -> Flask:
    app = Flask(__name__)

    def load():
        result = run_pipeline(Assumptions())
        arrivals = build_arrivals(result.ctx)
        con = st.connect(STATE_DB)
        if con.execute("SELECT COUNT(*) FROM exceptions").fetchone()[0] == 0:
            st.record_run(con, result, arrivals)
        ex = st.exceptions_with_decisions(con)
        return result, arrivals, con, ex

    def page_html(tab: str, body: str, n_open: int) -> str:
        return render_template_string(BASE, tabs=TABS, tab=tab, body=body, period=PERIOD, as_of=f"{AS_OF:%d %b %Y}", n_open=n_open)

    @app.route("/")
    def home():
        return redirect(url_for("page", tab="overview"))

    @app.route("/decide/<path:key>", methods=["POST"])
    def decide(key):
        con = st.connect(STATE_DB)
        try:
            st.record_decision(con, key, request.form["disposition"], request.form["by"].strip() or "unknown", request.form["why"])
        except (ValueError, KeyError) as exc:
            return f"<p>Not recorded: {exc}</p><p><a href='{url_for('page', tab='exceptions')}'>back</a></p>", 400
        return redirect(url_for("page", tab="exceptions") + f"#{key}")

    @app.route("/reset", methods=["POST"])
    def reset():
        st.reset_decisions(st.connect(STATE_DB))
        return redirect(url_for("page", tab="exceptions"))

    @app.route("/<tab>")
    def page(tab):
        result, arrivals, con, ex = load()
        roll, ctx = result.roll, result.ctx
        n_open = int((ex.disposition == "").sum())
        b = []
        if tab == "overview":
            cx = roll.complex_.set_index("line")["amount"]
            ms = roll.master.set_index("line")["amount"]
            crit = ex[ex.severity.isin(["critical", "high"])]
            b.append(f"""<div class="cards">
<div class="card"><div class="k">Complex NAV — Sep estimate</div><div class="v">${_m(cx['Complex NAV — September estimate (Σ vehicles)'])}</div><div class="s">Aug close ${_m(cx['Complex NAV — August close (Σ vehicles)'])}</div></div>
<div class="card"><div class="k">Master NAV — Sep estimate</div><div class="v">${_m(ms['Master NAV — September estimate'])}</div><div class="s">on clean statements: {cx['Share of master NAV on clean statements']:.1f}%</div></div>
<div class="card"><div class="k">Statements in</div><div class="v">{sum(1 for s in ctx.sleeves if ctx.statement_for(s))} / {len(ctx.sleeves)}</div><div class="s">{', '.join(s for s in ctx.sleeves if not ctx.statement_for(s)) or 'all received'} missing</div></div>
<div class="card"><div class="k">Exceptions</div><div class="v">{len(ex)}</div><div class="s">{(ex.severity=='critical').sum()} critical · {(ex.severity=='high').sum()} high · {n_open} undecided</div></div>
<div class="card"><div class="k">Cash held, excluded from NAV</div><div class="v">${_m(cx['Cash held unallotted (excluded from NAV)'])}</div><div class="s">unallotted subscription funds</div></div>
<div class="card"><div class="k">Range on disputed items</div><div class="v">+${_m(cx['Range: upside if disputed sleeve items resolve'])}</div><div class="s">if alternatives hold</div></div></div>""")
            b.append("<h2>What needs a decision before the estimate goes out</h2><table><tr><th>#</th><th>Sev.</th><th>Exception</th><th class=n>$ at stake</th><th>Disposition</th></tr>")
            for r in crit.itertuples():
                b.append(f"<tr><td>{r.rank}</td><td><span class='pill {r.severity}'>{r.severity}</span></td><td><a href='{url_for('page', tab='exceptions')}#{r.key}'>{r.title}</a></td><td class=n>{_m(r.amount_impact_usd)}</td><td>{r.disposition or '<span class=st-bad>open</span>'}</td></tr>")
            b.append("</table>")
            b.append("<div class=row><div><h2>Vehicles — September estimate</h2><table><tr><th>Vehicle</th><th class=n>Aug NAV</th><th class=n>Sep est.</th><th class=n>Return</th></tr>")
            for r in roll.vehicles.itertuples():
                b.append(f"<tr><td>{r.vehicle}</td><td class=n>{_m(r.aug_nav)}</td><td class=n>{_m(r.sep_nav_estimate)}</td><td class=n>{_pct(r.sep_return_pct)}</td></tr>")
            b.append("</table></div><div><h2>Arrivals</h2><table><tr><th>Item</th><th>Status</th></tr>")
            for r in arrivals[arrivals.kind.isin(["manager_statement", "direct_valuation", "our_deliverable"])].itertuples():
                b.append(f"<tr><td>{r.item}</td><td class='{_status_class(r.status)}'>{r.status}</td></tr>")
            b.append("</table></div></div>")

        elif tab == "inbox":
            b.append("<h2>Inbox — every message, classified by rule</h2><table><tr><th>#</th><th>Date</th><th>From</th><th>Subject</th><th>Category</th><th>Relevance</th><th>Rule</th><th>Attachment</th></tr>")
            for m in ctx.messages:
                cls = {"in_scope": "st-ok", "informational": "st-warn", "noise": "muted", "unknown": "st-bad"}[m.relevance]
                b.append(f"<tr><td>{m.msg_id}</td><td>{m.sent:%d %b}</td><td>{m.sender}</td><td>{m.subject}</td><td>{m.category}</td><td class='{cls}'>{m.relevance}</td><td class=small>{m.classification_rule}</td><td class=small>{m.attachment or ''}</td></tr>")
            b.append("</table>")

        elif tab == "arrivals":
            b.append("<h2>What has arrived, what is outstanding</h2><p class=muted>Expectations are derived from the reference data: one statement per sleeve, one valuation per direct position, one bank statement per cash balance, one acknowledgement per instruction, and our own deliverables.</p>")
            b.append("<table><tr><th>Item</th><th>Counterparty</th><th>Expected by</th><th>Received</th><th>Status</th><th>Document</th><th>Quality</th></tr>")
            for r in arrivals.itertuples():
                b.append(f"<tr><td>{r.item}</td><td>{r.counterparty}</td><td>{r.expected_by}</td><td>{r.received_on or ''}</td><td class='{_status_class(r.status)}'>{r.status}</td><td class=small>{r.document}</td><td class=small>{r.quality}</td></tr>")
            b.append("</table>")

        elif tab == "rollforward":
            b.append("<h2>Sleeves (master fund)</h2><table><tr><th>Sleeve</th><th class=n>Aug close</th><th class=n>Sep open (mgr)</th><th class=n>Open Δ</th><th class=n>Return</th><th class=n>Sep close (mgr)</th><th class=n>Estimate</th><th class=n>Alternative</th><th>Source</th><th>Basis</th></tr>")
            for r in roll.sleeves.itertuples():
                b.append(f"<tr><td>{r.sleeve}</td><td class=n>{_m(r.aug_close_nav)}</td><td class=n>{_m(r.sep_opening_per_manager)}</td><td class=n>{_m(r.opening_tie_diff)}</td><td class=n>{_pct(r.net_return_pct)}</td><td class=n>{_m(r.sep_closing_per_manager)}</td><td class=n><b>{_m(r.estimate_nav)}</b></td><td class=n>{_m(r.alt_nav)}</td><td>{r.estimate_source}</td><td class=small>{r.estimate_basis}<br><code>{r.source_doc}</code></td></tr>")
            b.append("</table><h2>Master fund</h2><table><tr><th>Line</th><th class=n>USD</th><th>Basis</th></tr>")
            for r in roll.master.itertuples():
                b.append(f"<tr><td>{r.line}</td><td class=n>{_m(r.amount)}</td><td class=small>{r.basis}</td></tr>")
            b.append("</table><h2>Vehicles</h2><table><tr><th>Vehicle</th><th class=n>Aug NAV</th><th class=n>Master P&L share</th><th class=n>Direct P&L</th><th class=n>Subs</th><th class=n>Reds</th><th class=n>Admin fee</th><th class=n>Sep NAV est.</th><th class=n>Return</th><th class=n>Cash held (ex-NAV)</th></tr>")
            for r in roll.vehicles.itertuples():
                b.append(f"<tr><td>{r.vehicle}</td><td class=n>{_m(r.aug_nav)}</td><td class=n>{_m(r.master_pnl_share)}</td><td class=n>{_m(r.direct_coinvest_pnl)}</td><td class=n>{_m(r.subscriptions)}</td><td class=n>{_m(r.redemptions)}</td><td class=n>{_m(r.admin_fee_accrual)}</td><td class=n><b>{_m(r.sep_nav_estimate)}</b></td><td class=n>{_pct(r.sep_return_pct)}</td><td class=n>{_m(r.cash_held_unallotted)}</td></tr>")
            b.append(f"</table><p class='muted small'>{roll.vehicles.basis.iloc[0]}. " + " ".join(r.direct_basis for r in roll.vehicles.itertuples() if r.direct_basis) + "</p>")
            b.append(f"<h3>Alternative scenario: Kestrel Point redemption processed at 30 Sep as the TA queued it</h3><table><tr><th>Vehicle</th><th class=n>Reds</th><th class=n>Sep NAV est.</th></tr>")
            for r in roll.scenario_vehicles.itertuples():
                b.append(f"<tr><td>{r.vehicle}</td><td class=n>{_m(r.redemptions)}</td><td class=n>{_m(r.sep_nav_estimate)}</td></tr>")
            b.append("</table><h2>Complex</h2><table><tr><th>Line</th><th class=n>USD / %</th></tr>")
            for r in roll.complex_.itertuples():
                b.append(f"<tr><td>{r.line}</td><td class=n>{_m(r.amount) if 'Share' not in r.line else _pct(r.amount)}</td></tr>")
            b.append("</table>")

        elif tab == "capital":
            b.append("<h2>Capital activity — where each item stands</h2><table><tr><th>Item</th><th>Kind</th><th>Investor</th><th>Vehicle</th><th class=n>Instructed</th><th class=n>Received</th><th class=n>Δ</th><th>Dealing</th><th>Funds value</th><th>Signed doc</th><th>TA ack</th><th>Status</th><th class=n>NAV effect</th><th>Cash</th><th>Sources</th></tr>")
            for r in roll.capital.itertuples():
                b.append(f"<tr><td>{r.item}</td><td>{r.kind}</td><td>{r.investor}</td><td>{r.vehicle}</td><td class=n>{_m(r.instructed)}</td><td class=n>{_m(r.received)}</td><td class=n>{_m(r.difference)}</td><td>{r.dealing_date}</td><td>{r.funds_value_date or ''}</td><td class=small>{r.signed_document}</td><td>{r.ta_acknowledged}</td><td class='{_status_class(r.status)}'>{r.status}</td><td class=n>{_m(r.nav_effect)}</td><td class=small>{r.cash_location}</td><td class=small>{r.source_docs}</td></tr>")
            b.append("</table><h2>Wires received</h2><table><tr><th>Wire</th><th>Account</th><th class=n>Amount</th><th>Value date</th><th>Originator</th><th>Reference</th><th>Source</th></tr>")
            for w in ctx.wires:
                b.append(f"<tr><td>{w.wire_id}</td><td>{w.account}</td><td class=n>{_m(w.amount)}</td><td>{w.value_date}</td><td>{w.originator}</td><td>{w.reference}</td><td class=small>{w.source_doc}</td></tr>")
            b.append("</table><h2>Investor register (August)</h2><table><tr><th>Investor</th><th>Vehicle</th><th class=n>31 Aug balance</th><th>Redemption terms</th></tr>")
            for r in ctx.ref.investors.itertuples():
                b.append(f"<tr><td>{r.investor}</td><td>{r.vehicle}</td><td class=n>{_m(r.aug_31_balance_usd)}</td><td>{r.redemption_terms}</td></tr>")
            b.append("</table>")

        elif tab == "exceptions":
            b.append(f"""<div class=banner><b>Human in the loop.</b> The system proposes; a person disposes. Every disposition needs a name and a reason and is appended to the log — nothing is overwritten.
<span class=small>APPROVE = accept the proposed treatment · REJECT = do not accept the figure/instruction · WAIVE = accept the risk · ESCALATE = above the Controller · REQUEST_INFO = needs something from outside · NOTE = acknowledged.</span>
<form method=post action="{url_for('reset')}" style="display:inline;float:right"><button onclick="return confirm('Clear every recorded decision?')" style="background:#fff;border:1px solid #c7641c;color:#c7641c;border-radius:6px;padding:4px 10px;cursor:pointer">Reset decision log</button></form></div>""")
            for r in ex.itertuples():
                findings = "".join(f"<li>{f}</li>" for f in r.findings)
                ev = " ".join(f"<code>{e}</code>" for e in r.evidence)
                dec = (f"<div class=dec><b>{r.disposition}</b> — {r.decided_by}, {r.decided_at} <span class=muted>({r.n_decisions} on record)</span><br>{r.rationale}</div>"
                       if r.disposition else "<div class='st-bad small' style='margin-top:6px'>No disposition recorded.</div>")
                b.append(f"""<details id="{r.key}" {'open' if not r.disposition or r.severity in ('critical','high') else ''}><summary>#{r.rank} <span class='pill {r.severity}'>{r.severity}</span> {r.title} <span class=muted>· {r.category.replace('_',' ')} · ${_m(r.amount_impact_usd)}{' · decision required' if r.decision_required else ''}</span></summary>
<p>{r.detail}</p>{'<ul class=small>' + findings + '</ul>' if findings else ''}<p class=ev><b>Evidence:</b> {ev}</p><p><b>Recommended:</b> {r.recommended_action}</p><p class='small muted'>key: <code>{r.key}</code></p>{dec}
<form class=decide method=post action="{url_for('decide', key=r.key)}"><label>Disposition</label><select name=disposition>{''.join(f'<option>{d}</option>' for d in st.DISPOSITIONS)}</select>
<label>Decided by</label><input name=by placeholder="Name (role)" value="Dennis Convery (Controller)"><label>Rationale</label><textarea name=why rows=2 placeholder="Required. Why — and what happens next."></textarea><button type=submit>Record decision</button></form></details>""")

        elif tab == "decisions":
            log = st.decision_log(con)
            b.append("<h2>Decision log — append-only</h2>")
            if log.empty:
                b.append("<p class=muted>No decisions recorded. Use the Exceptions tab, or <code>python -m fundops seed</code>.</p>")
            else:
                b.append("<table><tr><th>#</th><th>When</th><th>Exception</th><th>Disposition</th><th>By</th><th>Rationale</th></tr>")
                for r in log.itertuples():
                    b.append(f"<tr><td>{r.id}</td><td class=small>{r.decided_at}</td><td><a href='{url_for('page', tab='exceptions')}#{r.exception_key}'>#{r.rank} {r.title}</a></td><td><b>{r.disposition}</b></td><td>{r.decided_by}</td><td>{r.rationale}</td></tr>")
                b.append("</table>")

        elif tab == "assumptions":
            b.append("<h2>Modelling assumptions in force</h2><p class=muted>Each affects a published number. Change them in <code>fundops/config.py</code>; the roll-forward re-derives.</p><table><tr><th>Assumption</th><th>Setting</th><th>Reason</th></tr>")
            for a in roll.assumptions:
                b.append(f"<tr><td><code>{a['key']}</code></td><td>{a['statement']}</td><td>{a['reason']}</td></tr>")
            b.append("</table><h2>Fee schedule as parsed</h2><table><tr><th>Term</th><th>Value</th></tr>")
            f = ctx.ref.fees
            for k, v in (("management fee", f"{f.management_fee_rate_pa:.2%} p.a. — {f.management_fee_basis}"), ("incentive fee", f"{f.incentive_rate:.0%} — {f.incentive_basis}"),
                         ("administration fee", f"${f.admin_fee_per_vehicle_month:,.0f} per vehicle per month"), ("HWM as of", f.hwm_as_of)):
                b.append(f"<tr><td>{k}</td><td>{v}</td></tr>")
            for k, v in f.hwm.items():
                b.append(f"<tr><td class=small>HWM — {k}</td><td class=n>{_m(v)}</td></tr>")
            b.append("</table><h2>Checks passed this run</h2><table><tr><th>Check</th><th>Subject</th><th>Detail</th></tr>")
            for p in result.checks.passes:
                b.append(f"<tr><td class=small>{p['check']}</td><td>{p['subject']}</td><td class=small>{p['detail']}</td></tr>")
            b.append("</table>")
        else:
            return redirect(url_for("page", tab="overview"))
        return page_html(tab, "\n".join(b), n_open)

    return app
