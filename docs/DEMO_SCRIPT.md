# Video walkthrough — 8-minute script

Screens: a terminal and a browser at http://127.0.0.1:8765. Before recording: `python -m fundops reset` so the decision log is empty, then `python -m fundops run`.

**0:00 – 0:40 · Framing.** "One month of a fund-ops inbox: 28 messages, 7 attachments, 8 managers in 4 formats, a prior close and a fee schedule. Part 1 is what actually happened; Part 2 is the thing that means nobody reconstructs it by hand again. I built Part 2 to produce Part 1 — every number you'll see is computed from the pack, nothing is typed in. The repo has an independent script that re-derives every published figure from the raw files without using the system's code."

**0:40 – 1:30 · Run it.** Terminal: `python -m fundops run`. Point at the summary line: 28 messages, 7 September statements parsed, 0 parse failures, 17 exceptions, 69 checks passed. "Sixty-nine passes matter as much as seventeen failures — that's the list of what I trust and why."

**1:30 – 2:30 · Overview tab.** Cards: complex estimate $85.17M vs $83.62M; master $80.31M with 75.5% on clean statements; 7 of 8 statements in; $2.404M of cash held outside NAV. "The six things that need a decision before the estimate goes out are the six rows in that table."

**2:30 – 3:10 · Inbox tab.** "Every message classified by a *named* rule, so a reviewer can see why. The LedgerLoop pitch and the office move are noise; Harrowgate's flash is informational; the Wrenfield email is correctly read as an *August* statement, which is how the system knows September is missing."

**3:10 – 4:30 · Exceptions tab — the three kinds of problem.** Open #1: "An absence — Wrenfield sent nothing. The expected list is derived from the sleeve universe, so a missing statement is an exception, not a blank." Open #2: "A cross-document problem — Stonebow's statement is internally perfect and $185,000 off the August close. Only visible with two documents side by side. Here is the range." Open #5: "Inside a single document — Foxglove tells you in its own Notes column it used a quarterly rate on closing NAV; the schedule says average NAV, monthly. $19,126 overcharged." Then #3: "Kestrel: 22 days' notice against 30, and the TA queued it. The register, the instruction and the acknowledgement together."

**4:30 – 5:30 · Human in the loop.** On #3, record a disposition live: ESCALATE, your name, a rationale. Show it land in the decision log tab, attributed and timestamped. "The system never waives notice; it tells me the GP has to. The log is append-only — nothing is overwritten. If I re-ingest tomorrow with a new Wrenfield statement, that exception closes as resolved-by-data and every other decision survives." Then `python -m fundops seed` in the terminal to load the full set of dispositions.

**5:30 – 6:30 · Roll-forward and capital tabs.** Sleeve table: each row says whether the number is a statement figure, a disputed one, or a policy. Vehicles: Bellhaven's $724K in Select LP cash; Alderbrook's $2.404M held outside NAV; admin fee accrued; the Kestrel alternative scenario one line below. "The assumptions tab lists every policy with a reason. Change one in config and the whole thing re-derives."

**6:30 – 7:20 · The decision.** Open `docs/DECISION.md`. "Publish all three vehicles tomorrow with two disclosures — Wrenfield carried flat, Stonebow's $189K range. Hold Alderbrook and Kestrel out of NAV. Conservative on the two fee errors because correcting them only raises NAV. Two things that aren't NAV but *are* investor relations: Lanyard's confirmation due today and a twenty-business-day-old advisor question."

**7:20 – 8:00 · What's next and how I'd know.** From `docs/PLAN.md`: outbound actions from dispositions first (that's where the hours go), fund terms as reference data, multi-period state, LLM extraction *behind* the parsers with a human confirming mappings. "Within a month: oldest unanswered message ≤ 2 business days, every capital item dispositioned before Lanyard's deadline, and estimate-to-final movement explained by exceptions we'd already raised. If the list is short because nobody looked, the log shows the items ageing."

Close on the terminal: `python tests/verify_outputs.py` — ALL CHECKS PASSED.
