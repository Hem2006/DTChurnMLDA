"""Guardrails, validator, threshold math, twin edit restrictions, metrics, drift, and the case queue."""
import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from conftest import FIBER, NO_NET
from src import data as D
from src import db
from src import drift
from src import guardrails as G
from src import messages as MSG
from src import metrics as M
from src import threshold as T
from src import twin as TW

SAFE = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}


# ------------------------------------------------------------------ threshold
def test_cost_threshold_math():
    assert T.cost_threshold(10, 100, 0.5) == pytest.approx(0.2)
    assert T.cost_threshold(50, 100, 0.5) == pytest.approx(1.0)


@pytest.mark.parametrize('bad', [(0, 100, .5), (10, -1, .5), (10, 100, 0), (10, 100, 1.5), (True, 100, .5), (float('nan'), 100, .5)])
def test_cost_threshold_rejects_bad_numbers(bad):
    with pytest.raises(ValueError):
        T.cost_threshold(*bad)


def test_resolve_placeholder_until_all_three_set():
    r = T.resolve({'offer_cost': None, 'customer_value': 100, 'success_rate': 0.5}, 0.33)
    assert r['is_placeholder'] and r['threshold'] == 0.33 and r['source'] == T.PLACEHOLDER_SOURCE and 'PLACEHOLDER' in r['note']
    r = T.resolve({'offer_cost': 10, 'customer_value': 100, 'success_rate': 0.5}, 0.33)
    assert not r['is_placeholder'] and r['threshold'] == pytest.approx(0.2) and r['source'] == T.BUSINESS_SOURCE


def test_profit_curve_peaks_at_cost_threshold_on_calibrated_data():
    rng = np.random.default_rng(0)
    p = rng.random(60000)
    y = (rng.random(60000) < p).astype(int)                    # perfectly calibrated
    c, v, s = 10, 100, 0.5
    curve = pd.DataFrame(T.profit_curve(y, p, c, v, s, grid=np.linspace(0.02, 0.9, 89)))
    assert abs(curve.loc[curve.profit.idxmax(), 'threshold'] - T.cost_threshold(c, v, s)) < 0.05


# ------------------------------------------------------------------ guardrails
def test_policy_unset_means_needs_approval_even_with_known_state(policy):
    r = G.check_action({'discount_pct': 5}, SAFE, policy)
    assert r.status == 'needs_human_approval'
    assert {'contact_cap_unset', 'discount_cap_unset'} <= set(r.fired)


def test_vulnerable_and_escalated_escalate(limits):
    for st, rule in (({**SAFE, 'vulnerable': True}, 'vulnerable'), ({**SAFE, 'escalated': True}, 'open_escalation')):
        r = G.check_action({}, st, limits)
        assert r.status == 'blocked' and r.escalate_to_human and rule in r.fired


def test_unknown_state_needs_approval_never_permitted(limits):
    r = G.check_action({}, None, limits)
    assert r.status == 'needs_human_approval' and not r.escalate_to_human
    assert {'unknown_vulnerable', 'unknown_escalated'} <= set(r.fired)


def test_caps_enforced_with_limits(limits):
    assert G.check_action({'discount_pct': 10}, SAFE, limits).status == 'permitted'
    assert G.check_action({'discount_pct': 10}, {**SAFE, 'discount_pct_used_this_quarter': 10}, limits).status == 'blocked'
    assert G.check_action({}, {**SAFE, 'contacts_this_month': 2}, limits).status == 'blocked'
    assert G.check_action({'discount_pct': 10}, {**SAFE, 'discount_pct_used_this_quarter': None}, limits).status == 'needs_human_approval'


def test_policy_validation():
    with pytest.raises(ValueError):
        G.validate_policy({'pressure_language': ['x'], 'max_contacts_per_month': -1})
    with pytest.raises(ValueError):
        G.validate_policy({'pressure_language': []})


# ------------------------------------------------------------------ message validator
OFFER = MSG.offer_from_changes('combo', {'Contract': 'One year', 'TechSupport': 'Yes', 'discount_pct': 5})


def test_template_always_passes(policy):
    msg = MSG.template_message('Maria', OFFER, ['your plan type'])
    assert G.validate_message(msg, OFFER, policy, 'Maria') == []


@pytest.mark.parametrize('text, check', [
    ('Hi Maria, act now to get 5% off your monthly charge.', 'no_pressure_language'),
    ('Hi Maria, you get 10% off your monthly charge.', 'numbers_match_offer'),
    ('Hi Maria, enjoy a free upgrade.', 'no_unapproved_offer_elements'),
    ('Hi Maria, we know you might churn.', 'no_internal_words'),
    ('Hi Maria, your risk score is high.', 'no_internal_words'),
    ('Hi Maria, visit https://x.example now', 'no_links'),
    ('Hi Maria, enjoy twelve months at this price.', 'durations_match_offer'),
    ('Hello there, a one year contract awaits.', 'uses_first_name'),
    ('Hi Maria. ' + 'x' * 800, 'max_length'),
])
def test_validator_catches(text, check, policy):
    failed = {c['check'] for c in G.run_checks(text, OFFER, policy, 'Maria') if not c['passed']}
    assert check in failed


def test_validator_flags_terms_outside_the_offer(policy):
    offer = MSG.offer_from_changes('support only', {'TechSupport': 'Yes'})
    failed = {c['check'] for c in G.run_checks('Hi Maria, we can give you a discount and a new contract.', offer, policy, 'Maria') if not c['passed']}
    assert 'terms_match_offer' in failed


def test_llm_output_is_validated_and_falls_back(policy):
    class Fake:
        def __init__(self, text): self.text = text
        @property
        def messages(self): return self
        def create(self, **kw):
            from types import SimpleNamespace as N
            return N(content=[N(type='text', text=self.text)])
    good = MSG.draft('Maria', OFFER, [], policy, client=Fake('Hi Maria, we would love to offer you a one year contract, tech support added to your plan and 5% off your monthly charge. Just reply if interested.'))
    assert good['source'] == 'llm'
    bad = MSG.draft('Maria', OFFER, [], policy, client=Fake('Hi Maria, last chance: 50% off, free for a year!'))
    assert bad['source'] == 'template_fallback' and bad['rejected_reason'] and bad['rejected_text'] and G.validate_message(bad['message'], OFFER, policy, 'Maria') == []


def test_llm_never_chooses_the_offer(policy):
    seen = {}

    class Spy:
        messages = None
        def __init__(self): self.messages = self
        def create(self, **kw):
            from types import SimpleNamespace as N
            seen.update(kw)
            return N(content=[N(type='text', text='Hi Maria, a one year contract, tech support added to your plan and 5% off your monthly charge.')])
    MSG.draft('Maria', OFFER, [{'feature': 'Contract', 'value': 'Month-to-month'}], policy, client=Spy())
    prompt = seen['messages'][0]['content']
    assert OFFER['description'] in prompt and 'Month-to-month' not in prompt           # offer given as fixed facts; raw values never leak


def test_first_name_validation():
    for ok in ['Maria', "O'Neil", 'Anne-Marie', 'José']:
        assert MSG.validate_first_name(ok) == ok
    for bad in ['', '  ', 'R2D2', 'Robert; DROP TABLE', 'x' * 50]:
        with pytest.raises(ValueError):
            MSG.validate_first_name(bad)


# ------------------------------------------------------------------ twin restrictions
@pytest.fixture
def twin(meta):
    return TW.CustomerTwin(FIBER, meta)


@pytest.mark.parametrize('field', ['tenure', 'MonthlyCharges', 'TotalCharges', 'gender', 'SeniorCitizen', 'Partner', 'Dependents'])
def test_blocked_fields(twin, field):
    with pytest.raises(TW.TwinError, match='Cannot change'):
        twin.apply({field: 1})


def test_unknown_and_bad_values_rejected(twin):
    for ch in ({'Foo': 'x'}, {'Contract': 'Lifetime'}, {'discount_pct': 31}, {'discount_pct': -1}, {'discount_pct': True}):
        with pytest.raises(TW.TwinError):
            twin.apply(ch)


def test_addons_rejected_without_internet(meta):
    t = TW.CustomerTwin(NO_NET, meta)
    with pytest.raises(TW.TwinError, match='no internet'):
        t.apply({'TechSupport': 'Yes'})


def test_twin_is_immutable_and_apply_returns_new(twin):
    new = twin.apply({'Contract': 'One year', 'discount_pct': 10})
    assert twin.row['Contract'] == 'Month-to-month' and new.row['Contract'] == 'One year'
    assert new.row['MonthlyCharges'] == pytest.approx(72.0) and new.row['tenure'] == twin.row['tenure']
    with pytest.raises(AttributeError):
        twin.foo = 1
    with pytest.raises(TypeError):
        twin.row['tenure'] = 99


def test_discount_is_from_original_monthly_not_compounded(twin):
    assert twin.apply({'discount_pct': 10}).apply({'discount_pct': 10}).row['MonthlyCharges'] == pytest.approx(72.0)


def test_incoherent_customer_rejected(meta):
    with pytest.raises(TW.TwinError):
        TW.CustomerTwin({**FIBER, 'InternetService': 'No'}, meta)


# ------------------------------------------------------------------ metrics, drift
def test_delong_matches_sklearn_auc_and_detects_difference():
    rng = np.random.default_rng(1)
    y = rng.integers(0, 2, 2000)
    a = y + rng.normal(0, 1.0, 2000)
    b = y + rng.normal(0, 2.0, 2000)
    r = M.delong_test(y, a, b)
    assert r['auc_1'] == pytest.approx(roc_auc_score(y, a)) and r['auc_2'] == pytest.approx(roc_auc_score(y, b))
    assert r['p_value'] < 0.001
    same = M.delong_test(y, a, a)
    assert same['p_value'] == pytest.approx(1.0) and same['diff'] == 0


def test_ece_and_lift():
    y = np.array([0, 0, 1, 1] * 250)
    assert M.ece(y, np.where(y == 1, 1.0, 0.0)) == 0.0
    assert M.ece(np.zeros(100), np.full(100, 0.5)) == pytest.approx(0.5)
    p = np.linspace(0, 1, 1000)
    assert M.top_decile_lift((np.random.default_rng(0).random(1000) < p).astype(int), p) > 1.5


def test_psi_zero_for_same_data_and_large_for_shift(frame):
    tr, _ = D.split(frame)
    ref = drift.build_reference(tr)
    same = drift.check(tr, ref)
    assert (same.psi < 0.001).all()
    shifted = tr.assign(tenure=tr['tenure'] * 0.3, Contract='Month-to-month')
    s = drift.check(shifted, ref).set_index('feature')
    assert s.loc['tenure', 'status'] == 'MAJOR shift' and s.loc['Contract', 'status'] == 'MAJOR shift'


# ------------------------------------------------------------------ case queue
def _rec(ref, decision='recommend_pending_approval'):
    return {'customer_ref': ref, 'decision': decision, 'risk': {'probability': 0.7}, 'guardrail': {'status': 'needs_human_approval', 'approvals_needed': ['x']}}


def test_one_open_case_per_customer(tmp_path):
    p = tmp_path / 't.sqlite'
    cid = db.create_case(_rec('c1'), OFFER, 'Maria', 'msg', 'template', path=p)
    with pytest.raises(db.DuplicateOpenCase):
        db.create_case(_rec('c1'), OFFER, 'Maria', 'msg', 'template', path=p)
    db.reject(cid, 'Dana', 'not appropriate', path=p)
    assert db.create_case(_rec('c1'), OFFER, 'Maria', 'msg', 'template', path=p) != cid          # closed -> may reopen


def test_approval_needs_named_person_and_reject_needs_reason(tmp_path):
    p = tmp_path / 't.sqlite'
    cid = db.create_case(_rec('c2'), OFFER, 'Maria', 'msg', 'template', path=p)
    for who in ('', '   ', None):
        with pytest.raises(db.CaseError):
            db.approve(cid, who, path=p)
    with pytest.raises(db.CaseError):
        db.reject(cid, 'Dana', ' ', path=p)
    db.approve(cid, 'Dana', path=p)
    with pytest.raises(db.CaseError):
        db.approve(cid, 'Dana', path=p)                                    # no double decisions
    assert db.get_case(cid, path=p)['decided_by'] == 'Dana'


def test_export_writes_only_approved_and_marks_exported(tmp_path):
    p, out = tmp_path / 't.sqlite', tmp_path / 'outbox.csv'
    a = db.create_case(_rec('a'), OFFER, 'Ann', 'hello a', 'template', path=p)
    db.create_case(_rec('b'), OFFER, 'Bob', 'hello b', 'template', path=p)
    db.approve(a, 'Dana', path=p)
    rows = db.export_outbox(out, path=p)
    assert [r['customer_ref'] for r in rows] == ['a']
    assert pd.read_csv(out)['customer_ref'].tolist() == ['a']
    assert db.get_case(a, path=p)['status'] == 'exported' and db.export_outbox(out, path=p) == []


def test_escalated_and_no_action_are_closed_and_need_no_message(tmp_path):
    p = tmp_path / 't.sqlite'
    db.create_case({**_rec('e', 'escalate_to_human')}, path=p)
    db.create_case({**_rec('e', 'escalate_to_human')}, path=p)               # closed cases never block
    assert db.summary(path=p) == {'escalated': 2}
    with pytest.raises(db.CaseError):
        db.create_case(_rec('x'), path=p)                                    # a recommended case must carry a message
