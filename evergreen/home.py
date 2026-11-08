"""Home page (routed from app.py)."""
import streamlit as st

from src import ui

b = ui.setup('Model overview', '📊')
m = b['metrics']
st.markdown('A calibrated churn model, a what-if twin, deterministic guardrails and a human review queue, wired into one decision per customer. '
            'Use the pages in the sidebar: each is interactive and every number you see comes from the saved model or the config files.')
ui.threshold_banner(b)

c = st.columns(4)
ci = m['test_ci95']
c[0].metric('Test ROC-AUC', '%.3f' % m['test']['roc_auc'], help='95%% bootstrap CI [%.3f, %.3f], %d resamples' % (*ci['roc_auc'], m['bootstrap_resamples']))
c[1].metric('Brier (vs %.3f base-rate)' % m['brier_base_rate_predictor'], '%.3f' % m['test']['brier'])
c[2].metric('ECE (10 bins)', '%.3f' % m['test']['ece'])
c[3].metric('Served model', 'XGBoost + %s' % m['served']['calibration'])
st.caption('Held-out test split of %d customers (%.1f%% churn). Metrics are computed once on this split; CIs are percentile bootstrap intervals.' % (m['n_test'], 100 * m['churn_rate_test']))

st.subheader('Where to start')
l, r = st.columns(2)
with l:
    st.markdown("""
- **Customer Explorer**: pick a customer, see the calibrated risk, the reasons and the decision.
- **What-If Lab**: edit the levers the business controls and watch the score move (model sensitivity).
- **Cost Threshold Playground**: set cost, value and save-rate; watch the threshold and the confusion matrix move.
- **Calibration Lab**: uncalibrated vs Platt vs isotonic.
- **Guardrail Stress-Tester**: flip account state, and try to break the message validator.
""")
with r:
    st.markdown("""
- **Message Studio + Review Queue**: draft, validate, approve or reject, export.
- **Fairness Dashboard**: screening gaps by group, with a threshold slider.
- **Uplift View**: the SIMULATED four-quadrant picture.
- **Model Arena + Paper Mode**: model comparison with CIs, and the results tables.
- **Batch + Drift**: score a file, check a new month for drift.
""")

st.subheader('What this app will and will not claim')
st.markdown("""
| | |
|---|---|
| **Digital twin** | Model sensitivity, **not** a causal estimate. |
| **Uplift** | **SIMULATED** treatment; assumptions are in `config/uplift_sim.yaml`. Advisory only. |
| **Threshold** | A labelled **placeholder** until offer cost, customer value and success rate are set in `config/business.yaml`. |
| **Guardrails** | Plain code. Unknown account state or an unset policy limit always means *needs human approval*. |
| **Messages** | The LLM (if a key is set) only phrases a message for an offer already chosen; code validates every draft. |
| **Business value** | None is claimed. Profit curves use whatever numbers *you* type in, and say so. |
""")
st.caption('Artifacts: uplift model %s.' % ('built' if b['uplift'] else 'not built yet (run `python -m src.uplift`)'))
