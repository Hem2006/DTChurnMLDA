import pandas as pd
import plotly.express as px
import streamlit as st

from src import data as D
from src import ui
from src import uplift as U

b = ui.setup('Uplift View')
ui.simulated_banner()
ub = b['uplift']
if ub is None:
    st.warning('The SIMULATED uplift model has not been built yet.')
    if st.button('Build it now (takes a few seconds)'):
        with st.spinner('Training the SIMULATED T-learner...'):
            U.build(b)
        st.cache_resource.clear()
        st.rerun()
    st.stop()

cfg = ub['assumptions']
with st.expander('Simulation assumptions (arbitrary; editable in config/uplift_sim.yaml)'):
    st.json({k: v for k, v in cfg.items()})
    st.json(ub['evaluation'])
    st.caption('The correlation only shows the T-learner recovers the uplift the simulation itself built in. It says nothing about real offers.')

tr, te = D.split()
sc = U.score(te[D.INPUTS], ub, b['meta'])
thr = b['threshold']['threshold']
c = st.columns(2)
risk_cut = c[0].slider('Baseline-risk cut (default = threshold in force)', 0.05, 0.95, float(round(thr, 2)), 0.01)
min_up = c[1].slider('Minimum SIMULATED uplift to count as persuadable', 0.0, 0.3, float(cfg.get('persuadable_min_uplift', 0.02)), 0.01)
sc['quadrant'] = U.quadrant(sc['simulated_uplift'], sc['simulated_p_churn_control'], risk_cut, min_up)
sc['customerID'] = te['customerID'].values
colors = {'persuadable': ui.GREEN, 'sure thing': ui.BLUE, 'lost cause': ui.GREY, 'sleeping dog': ui.RED}
fig = px.scatter(sc, x='simulated_p_churn_control', y='simulated_uplift', color='quadrant', color_discrete_map=colors, hover_data=['customerID'], opacity=0.7,
                 labels={'simulated_p_churn_control': 'SIMULATED baseline churn risk (untreated)', 'simulated_uplift': 'SIMULATED uplift (churn avoided if offered)'})
fig.add_vline(x=risk_cut, line_dash='dash', line_color=ui.DARK)
fig.add_hline(y=min_up, line_dash='dash', line_color=ui.DARK)
fig.add_hline(y=0, line_color=ui.GREY)
fig.update_layout(height=540, title='SIMULATED four-quadrant view (held-out test customers)', margin=dict(t=110), legend=dict(y=1.12))
fig.add_annotation(text='SIMULATED', xref='paper', yref='paper', x=0.5, y=0.5, showarrow=False, font=dict(size=70, color='rgba(192,57,43,0.12)'))
st.plotly_chart(fig, width='stretch')

counts = sc['quadrant'].value_counts().reindex(U.QUADRANTS, fill_value=0)
cols = st.columns(4)
desc = {'persuadable': 'high risk, offer helps', 'sure thing': 'low risk, little to gain', 'lost cause': 'high risk, offer barely helps', 'sleeping dog': 'offer would raise churn'}
for col, q in zip(cols, U.QUADRANTS):
    col.metric(q, int(counts[q]), help=desc[q])
st.info('Worth knowing: the simulation assumes every retention probability is >= 0, so **no customer truly has negative uplift**. Any "sleeping dogs" here are estimation noise in the T-learner, which is exactly why this view is advisory only.')
st.caption('Quadrants: x = SIMULATED churn risk without the offer, y = SIMULATED uplift. Sleeping dogs have uplift < 0. Everything on this page is SIMULATED and advisory; the recommender never uses it to pick or veto an action.')
st.dataframe(sc.sort_values('simulated_uplift', ascending=False).head(15)[['customerID', 'simulated_p_churn_control', 'simulated_p_churn_treated', 'simulated_uplift', 'quadrant']], hide_index=True)
