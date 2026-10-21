"""Rule-based message classification.

Rules are ordered and each one names itself in ``classification_rule`` so a
reviewer can see *why* a message landed where it did. Anything no rule claims is
left ``unclassified`` and surfaced to a person — the classifier never guesses.

Categories
----------
prior_close_package, manager_statement, manager_flash, manager_marketing,
manager_admin_notice, subscription_instruction, redemption_instruction,
ta_acknowledgement, wire_credit, investor_query, auditor_request,
admin_calendar, bank_notice, internal_request, vendor_solicitation,
internal_admin, regulatory_newsletter, unclassified
"""
from __future__ import annotations

import re
from typing import Callable

from ..models import Message
from .reference import (ADMINISTRATOR_DOMAIN, AUDITOR_DOMAIN, BANK_DOMAIN,
                        INTERNAL_DOMAIN, MANAGER_DOMAINS)

Rule = tuple[str, Callable[[Message], bool], str, str]  # name, predicate, category, relevance


def _subj(m: Message) -> str:
    return m.subject.lower()


def _is_manager(m: Message) -> bool:
    return m.sender_domain in MANAGER_DOMAINS


RULES: list[Rule] = [
    ("admin.nav_package", lambda m: m.sender_domain == ADMINISTRATOR_DOMAIN and "nav package" in _subj(m),
     "prior_close_package", "in_scope"),
    ("admin.close_calendar", lambda m: m.sender_domain == ADMINISTRATOR_DOMAIN and "close calendar" in _subj(m),
     "admin_calendar", "in_scope"),
    ("admin.ta_ack", lambda m: m.sender_domain == ADMINISTRATOR_DOMAIN and "acknowledg" in _subj(m),
     "ta_acknowledgement", "in_scope"),
    ("manager.flash", lambda m: _is_manager(m) and re.search(r"flash|mid-month", _subj(m)) is not None,
     "manager_flash", "informational"),
    ("manager.marketing", lambda m: _is_manager(m) and re.search(r"investor letter|marketing", _subj(m) + m.body.lower()) is not None
     and not re.search(r"statement|figures|nav", _subj(m)),
     "manager_marketing", "noise"),
    ("manager.contact_change", lambda m: _is_manager(m) and "contact change" in _subj(m),
     "manager_admin_notice", "informational"),
    ("manager.statement", lambda m: _is_manager(m) and re.search(
        r"statement|monthly file|pack|figures|estimate|nav", _subj(m)) is not None,
     "manager_statement", "in_scope"),
    ("capital.subscription", lambda m: _subj(m).startswith("subscription"),
     "subscription_instruction", "in_scope"),
    ("capital.redemption", lambda m: _subj(m).startswith("redemption instruction"),
     "redemption_instruction", "in_scope"),
    ("bank.credit_advice", lambda m: m.sender_domain == BANK_DOMAIN and "credit advice" in _subj(m),
     "wire_credit", "in_scope"),
    ("bank.notice", lambda m: m.sender_domain == BANK_DOMAIN,
     "bank_notice", "informational"),
    ("investor.query", lambda m: "question from client" in _subj(m),
     "investor_query", "in_scope"),
    ("auditor.request", lambda m: m.sender_domain == AUDITOR_DOMAIN or "pbc" in _subj(m),
     "auditor_request", "in_scope"),
    ("internal.request", lambda m: m.sender_domain == INTERNAL_DOMAIN and "estimate" in _subj(m),
     "internal_request", "in_scope"),
    ("internal.admin", lambda m: m.sender_domain == INTERNAL_DOMAIN,
     "internal_admin", "noise"),
    ("vendor.solicitation", lambda m: m.sender.startswith("sales@") or "demo" in _subj(m),
     "vendor_solicitation", "noise"),
    ("newsletter", lambda m: "briefing" in _subj(m) or m.sender.startswith("briefings@"),
     "regulatory_newsletter", "noise"),
]


def _counterparty(m: Message) -> str | None:
    if m.sender_domain in MANAGER_DOMAINS:
        return MANAGER_DOMAINS[m.sender_domain]
    if m.sender_domain == ADMINISTRATOR_DOMAIN:
        return "Lanyard Fund Services (Cayman) Ltd"
    if m.sender_domain == BANK_DOMAIN:
        return "Granite Harbor Bank, N.A."
    if m.sender_domain == AUDITOR_DOMAIN:
        return "Whitlock & Sayre LLP"
    if m.sender_domain == INTERNAL_DOMAIN:
        return "Brindlewood (internal)"
    # investor / advisor: the signature line of the body, if it looks like one
    lines = [ln.strip() for ln in m.body.strip().splitlines() if ln.strip()]
    if lines:
        last = lines[-1]
        if len(last) <= 50 and not last.endswith((".", "?", "!")):
            return last.split(",", 1)[1].strip() if "," in last else last
    return None


def classify_messages(messages: list[Message]) -> list[Message]:
    for m in messages:
        for name, pred, cat, rel in RULES:
            if pred(m):
                m.category, m.relevance, m.classification_rule = cat, rel, name
                break
        else:
            m.category, m.relevance, m.classification_rule = "unclassified", "unknown", "none"
        m.counterparty = _counterparty(m)
    return messages
