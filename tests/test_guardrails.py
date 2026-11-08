import pytest

from app import guardrails as g
from conftest import ROOT

RULES = {'max_discount_pct_per_customer_per_quarter': 15, 'max_contacts_per_month': 2,
         'pressure_language': g.load_guardrails()['pressure_language']}
OK = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}


def test_shipped_limits_are_null_not_invented():
    r = g.load_guardrails(ROOT / 'guardrails.yaml')
    assert r['max_discount_pct_per_customer_per_quarter'] is None and r['max_contacts_per_month'] is None and r['pressure_language']


@pytest.mark.parametrize('bad', [-1, 'ten', True])
def test_bad_limits_rejected(tmp_path, bad):
    p = tmp_path / 'g.yaml'
    p.write_text('max_contacts_per_month: %r\npressure_language: [hurry]\n' % bad)
    with pytest.raises(ValueError):
        g.load_guardrails(p)


def test_empty_pressure_list_rejected(tmp_path):
    p = tmp_path / 'g.yaml'
    p.write_text('pressure_language: []\n')
    with pytest.raises(ValueError):
        g.load_guardrails(p)


def test_permitted_when_everything_verified():
    r = g.check_action({'discount_pct': 5}, OK, RULES)
    assert r.status == 'permitted' and r.contact_allowed and not r.reasons and not r.escalate_to_human


@pytest.mark.parametrize('flag', ['vulnerable', 'escalated'])
def test_vulnerable_or_escalated_never_contacted_and_escalated(flag):
    r = g.check_action({'discount_pct': 0}, {**OK, flag: True}, RULES)
    assert r.status == 'blocked' and r.escalate_to_human and not r.contact_allowed and flag in r.violations[0]
    # even with no limits configured, and even for a no-discount action
    assert g.check_action({}, {flag: True}, {'pressure_language': ['x'], 'max_contacts_per_month': None,
                                              'max_discount_pct_per_customer_per_quarter': None}).status == 'blocked'


def test_monthly_contact_limit():
    assert g.check_action({}, {**OK, 'contacts_this_month': 1}, RULES).status == 'permitted'
    r = g.check_action({}, {**OK, 'contacts_this_month': 2}, RULES)
    assert r.status == 'blocked' and 'contact limit' in r.violations[0] and not r.escalate_to_human


def test_quarterly_discount_cap():
    assert g.check_action({'discount_pct': 5}, {**OK, 'discount_pct_used_this_quarter': 10}, RULES).status == 'permitted'   # 15 == cap
    r = g.check_action({'discount_pct': 10}, {**OK, 'discount_pct_used_this_quarter': 10}, RULES)
    assert r.status == 'blocked' and 'quarterly cap' in r.violations[0]


def test_non_discount_action_ignores_discount_cap():
    assert g.check_action({'discount_pct': 0}, {**OK, 'discount_pct_used_this_quarter': 99}, RULES).status == 'permitted'


def test_unconfigured_limits_need_human_approval_never_auto_permit():
    none = {**RULES, 'max_contacts_per_month': None, 'max_discount_pct_per_customer_per_quarter': None}
    r = g.check_action({'discount_pct': 5}, OK, none)
    assert r.status == 'needs_human_approval' and len(r.approvals_needed) == 2 and r.contact_allowed
    assert g.check_action({}, OK, none).status == 'needs_human_approval'       # contact cap alone


def test_missing_account_state_needs_approval():
    r = g.check_action({'discount_pct': 5}, None, RULES)
    assert r.status == 'needs_human_approval'
    text = ' '.join(r.approvals_needed)
    for k in ('vulnerable', 'escalated', 'contacts_this_month', 'discount_pct_used_this_quarter'):
        assert k in text


def test_blocked_beats_approval():
    r = g.check_action({'discount_pct': 50}, {'vulnerable': False, 'escalated': False, 'contacts_this_month': 5}, RULES)
    assert r.status == 'blocked' and r.violations


@pytest.mark.parametrize('text', ['Act now to keep this offer', 'This is your LAST CHANCE', "Don't miss out!", 'Don’t miss out',
                                  'you must call us', 'Hurry, it is a limited  time deal', 'Offer is guaranteed'])
def test_pressure_language_detected(text):
    assert g.pressure_violations(text, RULES)


@pytest.mark.parametrize('text', ['Hi Sam, we would like to offer you a one year contract.', 'We appreciate your business, Sam.',
                                  'The urgentcare clinic is unrelated', 'Thanks for staying with us'])
def test_clean_text_passes(text):
    assert g.pressure_violations(text, RULES) == []
