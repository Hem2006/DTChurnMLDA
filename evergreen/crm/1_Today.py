import streamlit as st

from src import auth
from src import crm
from src import ui

b = ui.crm_setup('Today', '🌲', perm=None)


user = auth.current()
st.caption('Signed in as **%s** (%s). %s' % (user['name'], auth.ROLE_LABEL[user['role']], auth.ROLE_BLURB[user['role']]))


def link(path, label, icon, perm='lookup'):
    if not auth.can(user, perm):
        return
    try:
        st.page_link(path, label=label, icon=icon)
    except st.errors.StreamlitPageNotFoundError:        # page run outside the router (tests)
        st.write('**%s**' % label)


st.markdown('Find customers who are likely to leave, see a suggested offer, and get a message approved. Nothing is sent from here: approved messages are exported as a file for your sending tool.')

n = crm.queue_counts()
c = st.columns(4)
c[0].metric('Waiting for approval', n['waiting'])
c[1].metric('Approved, ready to export', n['approved'])
c[2].metric('Open escalations', n['escalations'])
c[3].metric('Rejected', n['rejected'])
if n['waiting']:
    st.warning('%d message(s) are waiting for someone to approve or reject them.' % n['waiting'])

st.subheader('What would you like to do?')
l, m, r = st.columns(3)
with l:
    st.markdown('**Look at one customer**')
    st.caption('See how likely they are to leave, why, and what to offer. Write and submit the message.')
    link('crm/2_Customer.py', 'Open Customer', '🔎', 'lookup')
with m:
    st.markdown('**Check a whole list**')
    st.caption('Upload a file of customers. Get them ranked by risk with a suggested next step.')
    link('crm/3_Customer_List.py', 'Open Customer list', '📋', 'lookup')
with r:
    st.markdown('**Approve messages**')
    st.caption('Review drafted messages, approve or reject them, and export the approved ones.')
    link('crm/4_Approvals.py', 'Open Approvals', '✅', 'view_all' if auth.can(user, 'view_all') else 'view_own')

st.subheader('How it works')
st.markdown("""
1. **Check**: the system estimates each customer's chance of leaving and why.
2. **Suggest**: for customers at high risk it suggests one offer, unless a safety rule says not to contact them.
3. **Approve**: a named person approves every message before it is exported.
""")

notices = crm.setup_notices(b)
if notices:
    with st.expander('Setup notes for your admin'):
        for x in notices:
            st.write('- ' + x)
with st.expander('How to read the numbers'):
    st.markdown("""
- **Chance of leaving**: out of 100 customers with the same profile, about that many left in past data. It is an estimate, not a certainty.
- **Risk bands**: *High* means the system would suggest an offer; *Some* is half way there; *Low* is not worth contacting.
- **Suggested offers** are what the model reacts to. They are **not proof** the offer will keep the customer.
- **Safety rules** always win. Customers marked vulnerable, or with an open complaint, are never offered anything; they go to a specialist. If the system does not know, a manager has to approve.
- The wording of every message is checked by the system: no pressure, no extra promises, nothing beyond the approved offer.
""")
st.caption('Analyst or data team? Switch on "Analyst tools" in the sidebar for model quality, fairness checks and the what-if lab.')
