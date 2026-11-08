from experiments.guardrail_ablation import decide, rule_of, simulated_states

NULL = {'max_contacts_per_month': None, 'max_discount_pct_per_customer_per_quarter': None, 'pressure_language': ['act now']}
LIM = {**NULL, 'max_contacts_per_month': 2, 'max_discount_pct_per_customer_per_quarter': 15}
CANDS = [{'name': 'price_cut_10', 'discount_pct': 10}, {'name': 'contract_one_year', 'discount_pct': 0}]
CLEAN = {'vulnerable': False, 'escalated': False, 'contacts_this_month': 0, 'discount_pct_used_this_quarter': 0}


def test_off_takes_top_candidate():
    assert decide(CANDS, None, NULL, enabled=False)[:2] == ('recommend', 'price_cut_10')
    assert decide([], None, NULL, enabled=False)[0] == 'no_action'


def test_null_limits_never_auto_recommend():
    for st in (None, CLEAN):
        d, name, hits = decide(CANDS, st, NULL)
        assert d == 'recommend_pending_approval' and name == 'price_cut_10'
        assert hits['contact_cap_unset'] == 1 and hits['discount_cap_unset'] == 1
    assert decide(CANDS, None, NULL)[2]['unknown_state'] >= 1


def test_vulnerable_escalates():
    d, name, hits = decide(CANDS, {**CLEAN, 'vulnerable': True}, LIM)
    assert d == 'escalate_to_human' and name is None and hits == {'vulnerable_account': 1}


def test_limits_block_and_fall_through():
    d, name, hits = decide(CANDS, {**CLEAN, 'discount_pct_used_this_quarter': 10}, LIM)
    assert (d, name) == ('recommend', 'contract_one_year') and hits['discount_cap_exceeded'] == 1
    d, _, hits = decide(CANDS, {**CLEAN, 'contacts_this_month': 2}, LIM)
    assert d == 'no_action' and hits['contact_cap_exceeded'] == 2
    assert decide(CANDS, CLEAN, LIM)[:2] == ('recommend', 'price_cut_10')


def test_rule_names_and_states():
    assert rule_of('vulnerable flag not supplied: x') == 'unknown_state'
    assert rule_of('account is escalated (open complaint/dispute)') == 'escalated_account'
    s = simulated_states(500)
    assert len(s) == 500 and any(x is None for x in s) and any(x and x['vulnerable'] for x in s)
    assert s == simulated_states(500)
