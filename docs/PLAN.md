# Part 2 — The system, and what I'd build next

## Where the boundary sits

The working slice does the part of the month that should never need a person and stops precisely where judgment starts.

**It does itself:** read every message, classify it by a named rule, pull figures out of whatever shape a manager sent them in (CSV, XLSX, an email body, a fixed-width dump), infer the unit a manager meant by "net return" from the NAV movement rather than trusting the label, tie every statement to the prior close, to its own arithmetic and to the fee schedule, match instructions to wires and wires to dealing dates, read notice periods off the register, derive the list of what *should* have arrived from the reference data, roll the month forward under stated policies, and rank what's wrong by severity and dollars.

**It leaves to a person:** every disposition. The system never decides to waive a notice period, allot a short wire, or accept a disputed opening balance. It proposes a treatment, shows its evidence and its arithmetic, and records who decided what and why. Modelling policies (carry a missing sleeve flat, hold subscription cash in the vehicle, allocate P&L on prior ownership) are declared in one file with a reason each, so they can be argued with rather than discovered.

**It deliberately does not:** use an LLM to read documents. Every number in the estimate has to be reproducible by a reviewer with the pack and a calculator, and this month's formats were all parseable by rules. Where that stops being true is the first thing on the list below.

## What it still can't do (honest list)

- A new manager format needs a parser written; it fails loudly into the "unparseable" queue rather than guessing.
- It reads the investor register for redemption terms but has no fund terms (dealing days, minimums, lock-ups, gates) — the 15 September subscription dealing day is accepted, not verified.
- Vehicle-level roll-forward is monthly and single-currency; no equalisation, no series accounting, no mid-month P&L on the Bellhaven cash.
- It doesn't send anything. Chasers, confirmations and investor replies are drafted in the Controller's head, not by the system.
- Master cash is carried flat because no bank statement is in the pack; there is no bank feed.
- One period, one pack. State persists in SQLite but there is no notion of "October" yet.

## What I'd build next, in order

1. **Outbound actions from dispositions (week 1).** Every disposition with a counterparty generates the email: the chaser to Wrenfield, the query to Stonebow, the capital confirmation to Lanyard, the reply to Rowan Hill. Drafted by template from the exception's evidence, sent only on a human click. This is where the hours actually go, and where a three-week-old advisor question stops happening.
2. **Fund terms as reference data (week 1).** Dealing frequency, notice, minimums, gates, side letters per vehicle and investor class. Turns two "accepted as instructed" items into real checks and makes the notice-period check general.
3. **Multi-period state (week 2).** Roll October on September's estimate and then on September's final; carry open exceptions forward; show what changed between estimate and final per sleeve so the estimate-to-final error becomes a tracked metric per manager.
4. **LLM-assisted extraction behind the deterministic parsers (week 2–3).** For a statement no parser claims, a model proposes field mappings with cited spans; a person confirms; the confirmed mapping becomes a parser fixture. The model never produces a number that goes into NAV without a human having seen the source next to it.
5. **Bank and administrator feeds (week 3–4).** Granite Harbor statement into master and vehicle cash; Lanyard's NAV package as a structured feed rather than a CSV attachment, with the vehicle-level return we had to ask for this month.
6. **Manager scorecard (week 4).** Timeliness, format stability, internal ties, fee accuracy, estimate-to-final drift per manager, by month. Foxglove and Harrowgate would already be on it.

Not on the list: a prettier UI, auth, deployment. A rough tool that catches the Stonebow break beats a polished one that doesn't.

## How I'd know within a month whether it was working

Measured on the October close against this September baseline:

| Metric | September (by hand, this pack) | October target |
|---|---|---|
| Statements auto-parsed with no human mapping | 7 of 7 received | 8 of 8 |
| Exceptions found by the system before a person went looking | 17, found on 1 Oct | all material items surfaced the day the triggering document arrives, not on T+1 |
| Oldest unanswered counterparty message at estimate date | 20 business days | ≤ 2 business days |
| Capital items with a recorded disposition before the estimate goes out | 3 of 3 (after the fact) | 100%, before Lanyard's confirmation deadline |
| Estimate-to-final NAV movement explained by a pre-identified exception | n/a | ≥ 90% of the movement, by dollars |
| Fee overcharges caught before payment | 2 ($34,004) | every statement checked against the schedule on arrival |
| Controller hours from last statement to estimate | ~1 day | half a day, with the time spent on decisions, not reading |

If in a month the exception list is shorter because the system is catching things earlier and the counterparties are responding faster, it's working. If the list is shorter because nobody is looking at it, the decision log will show that too — undecided items age in plain sight.
