"""Non-UI logic for the Streamlit dashboard (kept separate so it is unit-tested)."""
from __future__ import annotations

import warnings
from pathlib import Path

import pandas as pd

import churn_serving as cs
from app import guardrails as gr
from app import twin as tw
from app import uplift as up
from app.fairness import load_test_split

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv'


def load_bundle(root=ROOT) -> dict:
    b = cs.load_bundle(root)
    b['scenarios'] = tw.load_scenarios(Path(root) / 'scenarios.yaml')
    b['guardrails'] = gr.load_guardrails(Path(root) / 'guardrails.yaml')
    b['uplift'] = up.load(Path(root) / up.ARTIFACT)
    return b


def load_customers(csv=CSV) -> pd.DataFrame:
    df = pd.read_csv(csv)
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    return df.set_index('customerID')


def test_split_ids(csv=CSV) -> list[str]:
    X, _ = load_test_split(csv)
    return list(pd.read_csv(csv).loc[X.index, 'customerID'])


def to_model_row(row: pd.Series, meta: dict) -> dict:
    """A customers-table row as the plain dict of the 17 model inputs (python types)."""
    return {c: (row[c].item() if hasattr(row[c], 'item') else row[c]) for c in meta['input_columns']}


def overview(bundle: dict, customers: pd.DataFrame) -> dict:
    """Share flagged and (only if customer_value is configured) revenue at risk. Never invents a value."""
    cfg = bundle.get('config') or {}
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(customers, bundle)
    flagged = pred['flagged']
    out = {'n': int(len(pred)), 'n_flagged': int(flagged.sum()), 'share_flagged': float(flagged.mean()),
           'mean_probability': float(pred['churn_probability'].mean()), 'threshold': float(pred['threshold'].iloc[0]),
           'threshold_source': pred['threshold_source'].iloc[0], 'threshold_is_placeholder': bool(pred['threshold_is_placeholder'].iloc[0]),
           'by_contract': pd.DataFrame({'flagged_share': flagged.groupby(customers['Contract']).mean(),
                                        'n': customers['Contract'].value_counts()}),
           'probabilities': pred['churn_probability']}
    cv = cfg.get('customer_value')
    if cv is None:
        out.update(revenue_at_risk=None, revenue_at_risk_reason='not available: customer_value is not set in config.yaml')
    else:
        out.update(revenue_at_risk=float((pred['churn_probability'][flagged] * cv).sum()), revenue_at_risk_reason=None,
                   revenue_at_risk_definition='sum over flagged customers of churn probability x customer_value (config.yaml)')
    return out


def customer_view(bundle: dict, customer: dict) -> dict:
    """Probability, flag and full SHAP contributions (log-odds of the base XGBoost score) for one customer."""
    df = pd.DataFrame([customer])
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(df, bundle).iloc[0]
        contrib, base, margin = cs.explain(df, bundle)
        reasons = cs.top_reasons(df, bundle)
    c = contrib.iloc[0].sort_values(ascending=False)
    return {'prediction': pred, 'contributions': c, 'reasons': reasons.iloc[0]['reasons'] if len(reasons) else None}


def scenario_table(run: dict) -> pd.DataFrame:
    rows = []
    for r in run['results']:
        rows.append({'scenario': r['name'], 'risk now': r['baseline_risk'], 'risk after': r['risk_after'], 'risk drop (raw)': r['risk_drop'],
                     'risk drop (adjusted)': r['risk_drop_adjusted'], 'expected net value': r['expected_net_value'],
                     'flagged after': r['flagged_after'], 'confidence': r['confidence'],
                     'warning': ' | '.join(r['warnings']) if r['warnings'] else '', 'interpretation': r['interpretation']})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- user-supplied customers (builder + file scoring)
ADDONS = tw.ADDONS
NO_INTERNET, NO_PHONE = tw.NO_INTERNET, tw.NO_PHONE


def make_coherent(c: dict) -> dict:
    """Force the dependent fields a form must not let a user contradict (no internet -> add-ons n/a; no phone -> no lines)."""
    c = dict(c)
    if c['InternetService'] == 'No':
        for a in ADDONS:
            c[a] = NO_INTERNET
    else:
        for a in ADDONS:
            if c[a] == NO_INTERNET:
                c[a] = 'No'
    if c['PhoneService'] == 'No':
        c['MultipleLines'] = NO_PHONE
    elif c['MultipleLines'] == NO_PHONE:
        c['MultipleLines'] = 'No'
    return c


def score_file(bundle: dict, df: pd.DataFrame) -> dict:
    """Score a user's frame. Rows that fail validation or are incoherent are returned with the reason instead of being scored.
    Returns {'scored': DataFrame (probability, flagged, one column per input), 'rejected': DataFrame (row, reason)}."""
    meta = bundle['meta']
    cols = meta['input_columns']
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError('Missing required columns: %s' % missing)
    if len(df) == 0:
        raise ValueError('The file has no rows')
    if 'customerID' in df.columns:
        df = df.set_index('customerID')
    good, bad = [], []
    for idx, row in df[cols].iterrows():
        r = {c: (row[c].item() if hasattr(row[c], 'item') else row[c]) for c in cols}
        try:
            tw.CustomerTwin(r, meta)
            good.append(idx)
        except (tw.TwinError, ValueError) as e:
            bad.append({'row': idx, 'reason': str(e)})
    out = pd.DataFrame()
    if good:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            pred = cs.predict(df.loc[good, cols], bundle)
        out = pd.concat([pred[['churn_probability', 'flagged']], df.loc[good, cols]], axis=1)
    return {'scored': out, 'rejected': pd.DataFrame(bad, columns=['row', 'reason'])}
