import pandas as pd
import plotly.express as px
import streamlit as st

from src import explain as E
from src import ui
from src import recommender as R

b = ui.setup('Customer Explorer')
ui.threshold_banner(b)
cid, cust = ui.pick_customer('ce')
with st.expander('Account state (changes the decision, not the score)', expanded=False):
    state = ui.account_state_inputs('ce')
rec = R.recommend(cust, b, state, customer_ref=cid)
risk = rec['risk']

left, right = st.columns([2, 3])
left.plotly_chart(ui.gauge(risk['probability'], risk['threshold']), width='stretch')
left.caption('Dark line = threshold %.3f (%s). Flagged: **%s**.' % (risk['threshold'], risk['threshold_source'], 'yes' if risk['flagged'] else 'no'))
with right:
    st.subheader('Why the model leans this way')
    df = pd.DataFrame([cust])
    reasons = E.top_reasons(df, b)[0]
    if reasons:
        st.markdown(E.sentence(reasons))
        st.dataframe(pd.DataFrame([{'input': r['feature'], 'value': str(r['value']), 'push toward churn (log-odds)': r['push_log_odds']} for r in reasons]), hide_index=True)
    else:
        st.write('No input pushes this customer toward churn.')
    st.caption('SHAP averaged over the 5 base models of the calibrated ensemble. Describes what the model leans on; it is not a cause.')

st.subheader('Decision')
ui.decision_card(rec)
if rec['action']:
    a = rec['action']
    st.markdown('**Proposed action:** `%s` with changes `%s`' % (a['name'], a['changes']))
    st.caption('Chosen by the model-sensitivity twin (not causal) and checked by the guardrails; confidence: %s.' % a['confidence'])
if rec['simulated_uplift']:
    u = rec['simulated_uplift']
    st.warning('SIMULATED uplift %+.3f (churn %.3f untreated vs %.3f treated). Advisory only.' % (u['uplift'], u['p_churn_control'], u['p_churn_treated']))

with st.expander('All contributions'):
    c = E.contributions(df, b).iloc[0].sort_values()
    fig = px.bar(x=c.values, y=c.index, orientation='h', labels={'x': 'log-odds push (positive = toward churn)', 'y': ''})
    fig.update_traces(marker_color=[ui.RED if v > 0 else ui.GREEN for v in c.values])
    st.plotly_chart(fig, width='stretch')
with st.expander('Full case record (what would be stored)'):
    st.json(rec, expanded=False)
