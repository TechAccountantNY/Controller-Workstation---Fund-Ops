"""Controller Workstation — Fund Ops module.

Ingests a fund-operations inbox, reconstructs the month (sleeve → master →
vehicle → complex NAV roll-forward and capital-activity state), surfaces
ranked exceptions with cited evidence, and records human dispositions.

Everything numeric is computed from the files in ``data/``; nothing is typed in.
"""

__version__ = "0.1.0"
