import pandas as pd
import streamlit as st

from src import guardrails as G
from src import messages as MSG
from src import recommender as R
from src import ui

b = ui.setup('Guardrail Stress-Tester')
st.markdown('Guardrails are plain code. Flip the account state and watch the outcome change, then try to slip something past the message validator.')
tab1, tab2 = st.tabs(['Account state', 'Red-team the message validator'])

with tab1:
    ui.threshold_banner(b)
    cid, cust = ui.pick_customer('gs')
    with st.expander('Hypothetical policy limits (stress-test only: not saved, not the real policy)'):
        st.caption('The shipped policy leaves both limits unset, so everything needs approval. Try numbers here to see permitted / blocked paths.')
        k = st.columns(2)
        use = k[0].checkbox('Use hypothetical limits')
        cap_c = k[0].number_input('max_contacts_per_month', 0, 20, 2)
        cap_d = k[1].number_input('max_discount_pct_per_customer_per_quarter', 0, 100, 15)
        policy = {**b['policy'], 'max_contacts_per_month': cap_c, 'max_discount_pct_per_customer_per_quarter': cap_d} if use else b['policy']
        if use:
            st.warning('Hypothetical limits active.')
    state = ui.account_state_inputs('gs')
    rec = R.recommend(cust, {**b, 'policy': policy}, state, customer_ref=cid)
    st.metric('Churn probability', '%.1f%%' % (100 * rec['risk']['probability']), delta='flagged' if rec['risk']['flagged'] else 'not flagged', delta_color='off')
    ui.decision_card(rec)
    if not rec['risk']['flagged']:
        st.info('This customer is below the threshold, so guardrails are not reached. Tick "Only flagged" in the picker to see them act.')

    st.subheader('Same customer, different account states')
    presets = {'Everything unknown': dict(), 'Clean (no flags, 0 contacts, 0 discount used)': dict(vulnerable=False, escalated=False, contacts_this_month=0, discount_pct_used_this_quarter=0.0),
               'Vulnerable': dict(vulnerable=True, escalated=False, contacts_this_month=0, discount_pct_used_this_quarter=0.0),
               'Open escalation': dict(vulnerable=False, escalated=True, contacts_this_month=0, discount_pct_used_this_quarter=0.0),
               'Contact cap reached (3 contacts)': dict(vulnerable=False, escalated=False, contacts_this_month=3, discount_pct_used_this_quarter=0.0),
               'Discount nearly used up (14%)': dict(vulnerable=False, escalated=False, contacts_this_month=0, discount_pct_used_this_quarter=14.0)}
    rows = []
    for name, st_ in presets.items():
        r = R.recommend(cust, {**b, 'policy': policy}, st_, customer_ref=cid)
        rows.append({'account state': name, 'decision': r['decision'], 'action': (r['action'] or {}).get('name', ''),
                     'guardrails fired': ', '.join(ui.RULE_TEXT.get(f, f) for f in r['fired_by'])})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    if not use:
        st.caption('With the shipped policy (limits unset) even the "clean" row needs approval. Turn on hypothetical limits above to see "recommend".')

with tab2:
    st.markdown('Pick the **approved offer**, then write (or paste) a draft message. Every check is code; the LLM is not involved in judging.')
    offers = {s['name']: s['changes'] for s in b['scenarios'] if s['name'] != 'do_nothing'}
    name = st.selectbox('Approved offer', list(offers))
    offer = MSG.offer_from_changes(name, offers[name])
    st.info('Approved offer text: **%s**. Allowed numbers: %s.' % (offer['description'], offer['allowed_numbers'] or 'none'))
    first = st.text_input('Customer first name', 'Maria')
    attacks = {'(clean template)': None, 'Pressure': 'Hi {n}, this is your last chance: act now before it is too late!', 'Invented discount': 'Hi {n}, we are giving you 40% off for twelve months.',
               'Free stuff': 'Hi {n}, enjoy a free upgrade and a $50 credit.', 'Leaks internals': 'Hi {n}, our model says you are likely to churn, so here is an offer.',
               'Link': 'Hi {n}, claim it at https://example.com/win', 'Wrong offer terms': 'Hi {n}, we would like to give you a discount and a new contract.'}
    pick = st.selectbox('Load an attack', list(attacks))
    default = MSG.template_message(first or 'there', offer, ['your plan type']) if attacks[pick] is None else attacks[pick].format(n=first)
    text = st.text_area('Draft message', value=default, height=140, key='rt_' + pick + name + first)
    checks = G.run_checks(text, offer, b['policy'], first or None)
    ok = all(c['passed'] for c in checks)
    (st.success if ok else st.error)('ALL CHECKS PASSED: this text may be used.' if ok else 'REJECTED: %d check(s) failed.' % sum(not c['passed'] for c in checks))
    st.dataframe(pd.DataFrame([{'check': c['check'], 'result': 'PASS' if c['passed'] else 'FAIL', 'detail': c['detail']} for c in checks]).style.map(
        lambda v: 'background-color:#d7efe4' if v == 'PASS' else ('background-color:#f6d5d1' if v == 'FAIL' else ''), subset=['result']), hide_index=True, width='stretch')
    with st.expander('Pressure-language list'):
        st.write(', '.join(b['policy']['pressure_language']))
