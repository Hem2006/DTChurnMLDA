import pandas as pd
import streamlit as st

from src import auth
from src import crm
from src import db
from src import explain as E
from src import guardrails as G
from src import messages as MSG
from src import recommender as R
from src import ui

b = ui.crm_setup('Customer', '🔎')
user = auth.current()
can_submit = auth.can(user, 'submit')
cid, cust = ui.pick_customer_simple('cr')

with st.expander('What do we know about this customer\'s situation? (optional)'):
    st.caption('This is not in the customer data. If you pick "Not sure", a manager will have to approve any offer.')
    k = st.columns(4)
    opts = list(crm.TRI)
    v = crm.TRI[k[0].selectbox('Marked as vulnerable?', opts, key='cr_v')]
    e = crm.TRI[k[1].selectbox('Open complaint or dispute?', opts, key='cr_e')]
    n = k[2].number_input('Times contacted this month (-1 = not sure)', -1, 50, -1, key='cr_n')
    d = k[3].number_input('Discount % already given this quarter (-1 = not sure)', -1.0, 100.0, -1.0, key='cr_d')
state = {'vulnerable': v, 'escalated': e, 'contacts_this_month': None if n < 0 else int(n), 'discount_pct_used_this_quarter': None if d < 0 else float(d)}
rec = R.recommend(cust, b, state, customer_ref=cid)
risk = rec['risk']
bd = crm.band(risk['probability'], risk['threshold'])

st.subheader('How likely is this customer to leave?')
c = st.columns([1, 2])
c[0].metric('Chance of leaving', '%d%%' % round(100 * risk['probability']))
with c[1]:
    {'high': st.error, 'medium': st.warning, 'low': st.success}[bd]('**%s.** %s' % (crm.BAND_LABEL[bd], crm.one_in(risk['probability'])))

st.subheader('Why')
reasons = E.top_reasons(pd.DataFrame([cust]), b)[0]
if reasons:
    for r in reasons:
        st.write('- The customer %s.' % r['text'])
    st.caption('These are the things the system weighs most for this customer. They describe patterns in past customers, not the cause of any one decision.')
else:
    st.write('Nothing about this customer stands out as a reason to leave.')

st.subheader('Suggested next step')
kind = {'recommend': st.success, 'recommend_pending_approval': st.warning, 'no_action': st.info, 'escalate_to_human': st.error}[rec['decision']]
kind('**%s**' % crm.DECISION_PLAIN[rec['decision']])
offer_txt = crm.offer_text(rec)
if offer_txt:
    st.markdown('**Suggested offer:** %s' % offer_txt)
    st.caption('Chosen because the model reacts most to it for this customer. That is not proof it will work.')
why = crm.why_approval(rec)
if rec['decision'] == 'escalate_to_human':
    for w in why:
        st.write('- ' + w)
    if can_submit and st.button('Record that this was handed to a specialist'):
        try:
            db.create_case(rec, submitted_by=user['username'])
            st.success('Recorded.')
        except db.CaseError as ex:
            st.error(str(ex))
elif rec['decision'] == 'recommend_pending_approval':
    st.markdown('**A manager must approve because:**')
    for w in why:
        st.write('- ' + w)
elif rec['decision'] == 'no_action':
    st.write(rec['reasons'][0] if rec['risk']['flagged'] is False else 'No suitable offer is allowed for this customer right now.')

if rec['decision'].startswith('recommend'):
    st.subheader('Message')
    offer = MSG.offer_from_changes(rec['action']['name'], rec['action']['changes'])
    first = st.text_input('Customer first name', key='cr_first', help='The first name is not in the customer data, so type it here.')
    if not first.strip():
        st.info('Enter the customer\'s first name to see a draft message.')
    else:
        try:
            draft = MSG.draft(first, offer, rec['shap_reasons'], b['policy'])
        except ValueError as ex:
            st.error(str(ex))
            st.stop()
        text = st.text_area('Message (you can edit it)', value=draft['message'], height=140, key='cr_text_' + cid + draft['source'])
        checks = G.run_checks(text, offer, b['policy'], first.strip())
        bad = [x for x in checks if not x['passed']]
        if bad:
            for x in bad:
                st.error('Cannot be used: %s' % x['detail'])
        else:
            st.success('Message checks passed: no pressure wording, nothing beyond the suggested offer, correct name.')
        if not can_submit:
            st.info('Your role (%s) can look at customers but cannot send messages for approval.' % auth.ROLE_LABEL[user['role']])
        if can_submit and st.button('Send for approval', disabled=bool(bad), type='primary'):
            try:
                src = draft['source'] if text == draft['message'] else 'human_edited'
                db.create_case(rec, offer, MSG.validate_first_name(first), text, src, submitted_by=user['username'])
                db.log_message(cid, first, offer['name'], {**draft, 'message': text, 'source': src})
                st.success('Sent for approval. A manager will approve it. You can follow it under "Approvals".')
            except db.DuplicateOpenCase:
                st.error('This customer already has a message waiting. Decide that one first under "Approvals".')
            except (db.CaseError, ValueError) as ex:
                st.error(str(ex))

with st.expander('Details for analysts'):
    st.write('Probability %.4f; flag cutoff %.3f; decision code `%s`.' % (risk['probability'], risk['threshold'], rec['decision']))
    st.json(rec, expanded=False)
