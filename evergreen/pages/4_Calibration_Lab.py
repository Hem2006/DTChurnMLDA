import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sklearn.metrics import brier_score_loss

from src import metrics as M
from src import ui

b = ui.setup('Calibration Lab')
tp = ui.test_predictions()
y = tp['y'].values
variants = {'Uncalibrated XGBoost': 'p_xgboost', 'Platt (sigmoid)': 'p_xgb_platt', 'Isotonic': 'p_xgb_isotonic'}
served = b['metrics']['served']['calibration']
st.markdown('A calibrated 30%% score should churn about 30%% of the time. Toggle the methods to compare them on the held-out test split. '
            'The served model uses **%s** (chosen by out-of-fold Brier on the training split only; OOF Brier: %s).' % (
                {'isotonic': 'isotonic', 'sigmoid': 'Platt'}[served], ', '.join('%s %.4f' % (k, v) for k, v in b['metrics']['served']['oof_brier_train'].items())))
sel = st.multiselect('Show', list(variants), default=list(variants))
bins = st.slider('Reliability bins', 5, 20, M.ECE_BINS, help='The ECE reported everywhere else uses 10 equal-width bins.')

fig = go.Figure(go.Scatter(x=[0, 1], y=[0, 1], mode='lines', line=dict(dash='dash', color=ui.GREY), name='perfect'))
rows = []
for i, name in enumerate(sel):
    p = tp[variants[name]].values
    tbl = pd.DataFrame(M.reliability_table(y, p, bins))
    fig.add_trace(go.Scatter(x=tbl.mean_pred, y=tbl.observed, mode='lines+markers', name=name, marker=dict(size=np.clip(tbl.n / tbl.n.max() * 18, 5, 18)),
                             line=dict(color=ui.COLORWAY[i]), customdata=tbl.n, hovertemplate='predicted %{x:.2f}<br>observed %{y:.2f}<br>n=%{customdata}<extra>' + name + '</extra>'))
fig.update_layout(height=430, xaxis_title='mean predicted probability', yaxis_title='observed churn rate', xaxis_range=[0, 1], yaxis_range=[0, 1])
l, r = st.columns([3, 2])
l.plotly_chart(fig, width='stretch')
l.caption('Marker size = customers in the bin. Bins with few customers are noisy.')

base_rate = float(b['metrics']['brier_base_rate_predictor'])
for name in variants:
    p = tp[variants[name]].values
    rows.append({'method': name, 'ECE (10 equal-width bins)': M.ece(y, p), 'Brier': brier_score_loss(y, p), 'Brier skill vs base rate': 1 - brier_score_loss(y, p) / base_rate,
                 'mean prediction': float(p.mean())})
with r:
    st.subheader('Scores')
    st.dataframe(pd.DataFrame(rows).style.format({'ECE (10 equal-width bins)': '{:.4f}', 'Brier': '{:.4f}', 'Brier skill vs base rate': '{:.3f}', 'mean prediction': '{:.3f}'}), hide_index=True)
    st.metric('Base-rate predictor Brier', '%.4f' % base_rate, help='Always predict the training churn rate. A useful model must beat this.')
    st.caption('Observed test churn rate %.3f. ECE binning: %s.' % (y.mean(), M.ECE_BINNING))
    st.caption('Brier skill = 1 - Brier / Brier(base rate): above 0 means better than always predicting the base rate.')
hist = go.Figure()
for i, name in enumerate(sel):
    hist.add_trace(go.Histogram(x=tp[variants[name]], name=name, opacity=0.55, xbins=dict(start=0, end=1, size=0.05), marker_color=ui.COLORWAY[i]))
hist.update_layout(barmode='overlay', height=280, xaxis_title='predicted probability', yaxis_title='customers')
st.plotly_chart(hist, width='stretch')
