import pandas as pd
import plotly.express as px
import streamlit as st

from src import fairness as F
from src import ui

b = ui.setup('Fairness Dashboard')
tp = ui.test_predictions()
t0 = b['threshold']['threshold']
st.warning(F.CAVEAT + ' gender is audited only; it is not a model input.')
ui.threshold_banner(b)
t = st.slider('Threshold used for the audit', 0.05, 0.95, float(round(t0, 2)), 0.01, help='Starts at the threshold in force.')
res = F.audit(tp, t)
gaps, grp = res['gaps'], res['by_group']

st.subheader('Gaps (max - min across groups); highlighted when above %.2f' % F.GAP_FLAG)
piv = gaps.pivot(index='attribute', columns='metric', values='gap')[F.METRICS]
st.dataframe(piv.style.apply(lambda col: ['background-color:#f6d5d1' if v > F.GAP_FLAG else '' for v in col], axis=0).format('{:.3f}'), width='stretch')
for _, g in gaps[gaps.flagged].iterrows():
    st.error('**%s / %s** gap %.3f (%s highest, %s lowest; smallest group n = %d)' % (g.attribute, g.metric, g.gap, g.highest, g.lowest, g.min_group_n))
if not gaps.flagged.any():
    st.success('No gap above %.2f at this threshold (screening only).' % F.GAP_FLAG)

st.subheader('Per group')
metric = st.radio('Metric', F.METRICS, horizontal=True)
fig = px.bar(grp, x='attribute', y=metric, color='group', barmode='group', text=grp[metric].map('{:.2f}'.format), hover_data=['n'])
fig.update_layout(height=380, yaxis_range=[0, 1])
st.plotly_chart(fig, width='stretch')
st.dataframe(grp.style.format({m: '{:.3f}' for m in F.METRICS}), hide_index=True, width='stretch')

st.subheader('How the gaps change with the threshold')
rows = []
for x in [i / 20 for i in range(2, 19)]:
    gx = F.audit(tp, x)['gaps']
    rows += [{'threshold': x, 'attribute': r.attribute, 'metric': r.metric, 'gap': r.gap} for r in gx.itertuples()]
sweep = pd.DataFrame(rows)
m2 = st.radio('Gap metric', F.METRICS, horizontal=True, key='sweep_metric')
fig = px.line(sweep[sweep.metric == m2], x='threshold', y='gap', color='attribute', markers=True)
fig.add_hline(y=F.GAP_FLAG, line_dash='dash', line_color=ui.RED, annotation_text='%.2f flag line' % F.GAP_FLAG)
fig.add_vline(x=t, line_dash='dot', line_color=ui.DARK)
fig.update_layout(height=360)
st.plotly_chart(fig, width='stretch')
st.caption('Test split n = %d. Smallest groups are small (see n above), so gaps are noisy. No mitigation has been applied, and no group-specific thresholds are used.' % len(tp))
