"""Guardrails: deterministic code, no LLM anywhere in this file.

Account-level (check_action) -> status:
  permitted              every check passed with verified inputs
  needs_human_approval   a policy limit is unset, or the account state needed to verify a limit was not supplied
  blocked                a rule is violated; vulnerable or escalated accounts also set escalate_to_human (no contact at all)
Message-level (run_checks / validate_message): length, first name, pressure language, offer fidelity, internal words.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

LIMIT_KEYS = ('max_discount_pct_per_customer_per_quarter', 'max_contacts_per_month')
DEFAULT_MAX_CHARS = 700


def validate_policy(raw: Mapping) -> dict:
    for k in LIMIT_KEYS:
        v = raw.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            raise ValueError('%s must be null or a non-negative number, got %r' % (k, v))
    ph = raw.get('pressure_language')
    if not isinstance(ph, list) or not ph or not all(isinstance(x, str) and x.strip() for x in ph):
        raise ValueError('policy needs a non-empty pressure_language list')
    return {**{k: raw.get(k) for k in LIMIT_KEYS}, 'pressure_language': [x.strip() for x in ph], 'max_message_chars': raw.get('max_message_chars') or DEFAULT_MAX_CHARS}


@dataclass
class GuardrailResult:
    status: str = 'permitted'
    violations: list = field(default_factory=list)
    approvals_needed: list = field(default_factory=list)
    escalate_to_human: bool = False
    fired: list = field(default_factory=list)          # machine-readable names of the rules that fired

    def block(self, rule, why, escalate=False):
        self.violations.append(why)
        self.fired.append(rule)
        self.status = 'blocked'
        self.escalate_to_human = self.escalate_to_human or escalate

    def need_approval(self, rule, why):
        self.approvals_needed.append(why)
        self.fired.append(rule)
        if self.status != 'blocked':
            self.status = 'needs_human_approval'

    def as_dict(self):
        return {'status': self.status, 'violations': self.violations, 'approvals_needed': self.approvals_needed,
                'escalate_to_human': self.escalate_to_human, 'fired': self.fired}


def check_action(action: Mapping[str, Any], account_state: Mapping[str, Any] | None, policy: Mapping) -> GuardrailResult:
    """action: {'discount_pct': float}; account_state: vulnerable, escalated, contacts_this_month, discount_pct_used_this_quarter (None = unknown)."""
    res, st = GuardrailResult(), dict(account_state or {})
    if st.get('vulnerable') is True:
        res.block('vulnerable', 'account is flagged vulnerable: no contact; escalate to a human', escalate=True)
    if st.get('escalated') is True:
        res.block('open_escalation', 'account has an open escalation (complaint/dispute): no contact; escalate to a human', escalate=True)
    if res.status == 'blocked':
        return res
    for flag in ('vulnerable', 'escalated'):
        if not isinstance(st.get(flag), bool):
            res.need_approval('unknown_' + flag, '%s flag not supplied: cannot verify the account may be contacted' % flag)
    cap, used = policy.get('max_contacts_per_month'), st.get('contacts_this_month')
    if cap is None:
        res.need_approval('contact_cap_unset', 'max_contacts_per_month is not set in config/policy.yaml')
    elif used is None:
        res.need_approval('contacts_unknown', 'contacts_this_month not supplied: cannot verify the monthly contact cap (%s)' % cap)
    elif used + 1 > cap:
        res.block('contact_cap', 'monthly contact cap reached (%s of %s used)' % (used, cap))
    disc = float(action.get('discount_pct') or 0)
    if disc > 0:
        capd, usedd = policy.get('max_discount_pct_per_customer_per_quarter'), st.get('discount_pct_used_this_quarter')
        if capd is None:
            res.need_approval('discount_cap_unset', 'max_discount_pct_per_customer_per_quarter is not set in config/policy.yaml')
        elif usedd is None:
            res.need_approval('discount_unknown', 'discount_pct_used_this_quarter not supplied: cannot verify the quarterly discount cap (%s%%)' % capd)
        elif usedd + disc > capd:
            res.block('discount_cap', 'discount of %s%% would exceed the quarterly cap (%s%% used of %s%%)' % (disc, usedd, capd))
    return res


# ------------------------------------------------------------------ message validator
NUMBER_WORDS = ('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen '
                'twenty thirty forty fifty sixty seventy eighty ninety hundred half').split()
UNITS = r'(?:year|years|month|months|week|weeks|day|days|percent|%)'
FORBIDDEN = r'free|complimentary|gift|credit|credits|refund|refunds|waive|waived|bonus|voucher|upgrade|upgrades|cashback|rebate|reward|rewards|prize|dollars?|pounds?|euros?'
INTERNAL = (r'churn|churning|leave|leaving|cancel|cancelling|canceling|cancellation|risk|score|scored|model|algorithm|predict|predicted|prediction|'
            r'machine learning|\bai\b|data science|flagged')
DISCOUNT = r'discount|discounted|% off|percent off|off your|cheaper|price cut|lower price|reduced price|saving|savings|save'
CONTRACT = r'contract|annual|per year|12.month|twelve.month'
SUPPORT = r'tech support|technical support'


def pressure_violations(text: str, policy: Mapping) -> list[str]:
    norm, found = text.replace('’', "'"), []
    for ph in policy['pressure_language']:
        if re.search(r'(?<!\w)' + re.escape(ph).replace(r'\ ', r'\s+') + r'(?!\w)', norm, flags=re.IGNORECASE):
            found.append(ph)
    return found


def run_checks(text: str, offer: Mapping, policy: Mapping, first_name: str | None = None) -> list[dict]:
    """Every validator check with its pass/fail and detail. first_name=None skips the first-name check."""
    text = text if isinstance(text, str) else ''
    norm = text.replace('’', "'")
    low = norm.lower()
    checks = []

    def add(name, bad, ok_detail, bad_detail=None):
        checks.append({'check': name, 'passed': not bad, 'detail': ok_detail if not bad else (bad_detail or '; '.join(map(str, bad)))})

    add('not_empty', [] if text.strip() else ['empty message'], 'message has text')
    cap = policy.get('max_message_chars', DEFAULT_MAX_CHARS)
    add('max_length', [len(text)] if len(text) > cap else [], '%d of %d characters' % (len(text), cap), '%d characters (max %d)' % (len(text), cap))
    if first_name is not None:
        add('uses_first_name', [] if first_name.lower() in low else ['missing'], 'addresses the customer as %s' % first_name, 'does not address the customer by first name')
    add('no_pressure_language', pressure_violations(text, policy), 'no pressure phrases', None)
    allowed = {float(x) for x in offer['allowed_numbers']}
    nums = [n for n in re.findall(r'\d+(?:[.,]\d+)?', norm) if float(n.replace(',', '.')) not in allowed]
    add('numbers_match_offer', nums, 'only numbers from the approved offer', 'number(s) not in approved offer: %s' % ', '.join(nums))
    allowed_ph, dur = set(offer['allowed_phrases']), []
    for m in re.finditer(r'\b(%s)\b[\s\-]*(%s)(?![a-z])' % ('|'.join(NUMBER_WORDS), UNITS), low):
        ph = '%s %s' % (m.group(1), m.group(2).rstrip('s') if m.group(2) != '%' else '%')
        if ph not in allowed_ph:
            dur.append(m.group(0))
    add('durations_match_offer', dur, 'no durations or amounts outside the offer', 'amount/duration not in approved offer: %s' % ', '.join(dur))
    bad = re.findall(r'(?<!\w)(%s)(?!\w)' % FORBIDDEN, low) + re.findall(r'[$£€]', norm)
    add('no_unapproved_offer_elements', bad, 'no free/credit/refund/upgrade/currency wording', 'unapproved offer wording: %s' % ', '.join(sorted(set(bad))))
    terms = [label for pat, term, label in ((DISCOUNT, 'discount', 'a discount'), (CONTRACT, 'contract', 'a contract'), (SUPPORT, 'support', 'tech support'))
             if re.search(r'(?<!\w)(%s)(?!\w)' % pat, low) and term not in offer['allowed_terms']]
    add('terms_match_offer', terms, 'mentions only what the offer contains', 'mentions %s, not in the approved offer' % ' and '.join(terms))
    internal = re.findall(r'(?<!\w)(%s)(?!\w)' % INTERNAL, low)
    add('no_internal_words', internal, 'no internal analysis wording', 'internal wording: %s' % ', '.join(sorted(set(internal))))
    add('no_links', re.findall(r'https?://\S+|www\.\S+|@\w+', low), 'no links or addresses')
    return checks


def validate_message(text: str, offer: Mapping, policy: Mapping, first_name: str | None = None) -> list[str]:
    """Problems with a draft; an empty list means it may be used."""
    return ['%s: %s' % (c['check'], c['detail']) for c in run_checks(text, offer, policy, first_name) if not c['passed']]
