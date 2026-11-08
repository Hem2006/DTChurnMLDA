"""Guardrails, enforced in code (not only in prompts). Rules live in guardrails.yaml.

Outcome of check_action(): status is one of
  'permitted'            all checks passed with verified inputs
  'needs_human_approval' a limit is not configured, or the account state needed to verify it was not supplied
  'blocked'              a rule is violated (or the account is vulnerable/escalated -> escalate_to_human)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

ROOT = Path(__file__).resolve().parent.parent
LIMIT_KEYS = ('max_discount_pct_per_customer_per_quarter', 'max_contacts_per_month')


def load_guardrails(path=None) -> dict:
    p = Path(path or ROOT / 'guardrails.yaml')
    raw = yaml.safe_load(p.read_text(encoding='utf8')) or {}
    for k in LIMIT_KEYS:
        v = raw.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            raise ValueError('%s must be null or a non-negative number, got %r' % (k, v))
    phrases = raw.get('pressure_language')
    if not isinstance(phrases, list) or not phrases or not all(isinstance(x, str) and x.strip() for x in phrases):
        raise ValueError('guardrails.yaml needs a non-empty pressure_language list')
    return {**{k: raw.get(k) for k in LIMIT_KEYS}, 'pressure_language': [x.strip() for x in phrases]}


@dataclass
class GuardrailResult:
    status: str = 'permitted'
    violations: list = field(default_factory=list)          # reasons it is blocked
    approvals_needed: list = field(default_factory=list)    # reasons a human must approve
    escalate_to_human: bool = False
    contact_allowed: bool = True

    def block(self, why, escalate=False):
        self.violations.append(why)
        self.status, self.contact_allowed = 'blocked', False
        self.escalate_to_human = self.escalate_to_human or escalate

    def need_approval(self, why):
        self.approvals_needed.append(why)
        if self.status != 'blocked':
            self.status = 'needs_human_approval'

    @property
    def reasons(self):
        return self.violations + self.approvals_needed

    def as_dict(self):
        return {'status': self.status, 'violations': self.violations, 'approvals_needed': self.approvals_needed,
                'escalate_to_human': self.escalate_to_human, 'contact_allowed': self.contact_allowed}


def check_action(action: Mapping[str, Any], account_state: Mapping[str, Any] | None, rules: Mapping) -> GuardrailResult:
    """action: {'discount_pct': float (0 if none)}; account_state: vulnerable, escalated, contacts_this_month,
    discount_pct_used_this_quarter (all optional). A retention action always involves one contact."""
    res = GuardrailResult()
    st = dict(account_state or {})
    if st.get('vulnerable') is True:
        res.block('account is flagged vulnerable: no contact; escalate to a human', escalate=True)
    if st.get('escalated') is True:
        res.block('account is escalated (open complaint/dispute): no contact; escalate to a human', escalate=True)
    if res.status == 'blocked':
        return res                                                         # nothing else matters: no contact at all
    for flag in ('vulnerable', 'escalated'):
        if not isinstance(st.get(flag), bool):
            res.need_approval('%s flag not supplied: cannot verify the account may be contacted' % flag)

    cap = rules.get('max_contacts_per_month')
    used = st.get('contacts_this_month')
    if cap is None:
        res.need_approval('max_contacts_per_month is not configured in guardrails.yaml')
    elif used is None:
        res.need_approval('contacts_this_month not supplied: cannot verify the monthly contact limit (%s)' % cap)
    elif used + 1 > cap:
        res.block('monthly contact limit reached (%s of %s used)' % (used, cap))

    disc = float(action.get('discount_pct') or 0)
    if disc > 0:
        cap_d = rules.get('max_discount_pct_per_customer_per_quarter')
        used_d = st.get('discount_pct_used_this_quarter')
        if cap_d is None:
            res.need_approval('max_discount_pct_per_customer_per_quarter is not configured in guardrails.yaml')
        elif used_d is None:
            res.need_approval('discount_pct_used_this_quarter not supplied: cannot verify the quarterly discount cap (%s%%)' % cap_d)
        elif used_d + disc > cap_d:
            res.block('discount of %s%% would exceed the quarterly cap (%s%% used of %s%%)' % (disc, used_d, cap_d))
    return res


def pressure_violations(text: str, rules: Mapping) -> list[str]:
    """Pressure phrases found in text (case-insensitive, whole words). Empty list means clean."""
    found = []
    norm = text.replace('’', "'")
    for ph in rules['pressure_language']:
        if re.search(r'(?<!\w)' + re.escape(ph).replace(r'\ ', r'\s+') + r'(?!\w)', norm, flags=re.IGNORECASE):
            found.append(ph)
    return found
