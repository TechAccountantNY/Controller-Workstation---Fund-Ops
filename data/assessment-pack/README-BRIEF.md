# Equi assessment — MD, Agentic Fund Finance & Legal

Thanks for taking the time. We want to see how you think and how you build. There are two parts built on one set of materials. Part 1 asks you to tell us the truth about a messy month. Part 2 asks you to build the thing that would have made that easy. There's no detailed spec on purpose, and judgment calls we haven't made for you are the point.

One thing up front, because it changes how you should approach this. We are not testing whether you can do fund accounting by hand. We assume you can. We are testing what you build so that nobody has to.

## The materials

This pack is one month of a fund operations inbox for a fabricated fund complex: three vehicles feeding one master, eight underlying managers. The `inbox/` folder holds the messages in date order. `attachments/` holds the files referenced by them, including last month's closing NAV package and a one-page fee schedule.

None of it is real. The managers, the investors, the figures and the structure are all invented.

## Part 1: What Actually Happened

Reconstruct the month. We want the state a CFO would need on the first of the following month: a NAV roll-forward for each vehicle and the complex, the month's capital activity and where each item stands, and an exception list of everything that is missing, wrong, or doesn't tie, ranked by how much it matters.

We will tell you one thing so you don't spend the week wondering: there are problems in this month, and they are not all the same kind of problem. Some are inside a single document. Some only appear when you put two documents next to each other. At least one is an absence rather than a mistake.

Then one written decision. It is the first of the month and the investor estimate goes out tomorrow. Given what you found, what do you publish, what do you hold, and what do you say about the parts you're not sure of? A page at most.

## Part 2: The System

You just did this by whatever means you chose. Now build the thing that means nobody does it by hand again. A working slice, not a deck: something that ingests these messages, keeps a live view of what has arrived and what is outstanding, ties activity together, and surfaces the exceptions for a human instead of waiting for one to go looking.

Where the boundary of that system sits, what it does itself and what it leaves to a person, and what you'd build first are your calls, and they're the calls we care about most. Alongside the working slice, give us a short plan: what you'd build next, in what order, and how you'd know within a month whether it was working.

## Deliverables

- Working code in a GitHub repo or a zip
- Your Part 1 reconstruction: the roll-forward, the capital activity state, and the ranked exception list, in whatever form your work produces them
- The one-page written decision
- Your Part 2 plan, as a doc or a README section
- A 5 to 10 minute video walkthrough where you demo both parts and explain your decisions
- A README with setup instructions, plus the decisions and tradeoffs you made

## Timeline

7 days from receipt. Spend as much or as little time as you want. We evaluate the output, not the hours. As a rough guide, most people put in somewhere between six and ten hours across both parts.

## Tech guidance

Use whatever you'd normally reach for. Python, a notebook, a Next.js app, Streamlit, n8n, Claude or GPT with tools, an agent framework, a spreadsheet with something real behind it. We care about the result, not the stack.

Use AI freely. We'd be surprised if you didn't, and we'd rather see how you work with it than watch you perform not using it. What we'll ask in the follow-up is why your answer is right, where it would break, and what you checked before trusting it.

## A few notes

We evaluate the output holistically. Are the ideas genuinely yours, does the thing work, and did you make decisions you can explain?

We weight domain judgment and what you built above technical polish. A rough system that catches the right problems beats a beautiful one that misses them. We're not looking for a production deployment, CI/CD, or over-engineering.

There is no single right answer, and we don't have a model solution we're grading against. The written pieces matter as much as the code.

If anything's unclear, just reply to the email this came with. Looking forward to seeing what you build.

— Equi
