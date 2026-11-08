"""SIMULATED uplift (T-learner, plain scikit-learn + XGBoost).

The Telco data has no treatment column, so treatment and its outcome are SIMULATED from the explicit assumptions in
config.yaml (`uplift_simulation`). Everything this module returns is labelled SIMULATED. The numbers show that the pipeline works;
they are NOT findings about real retention offers and must not be reported as such.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

import churn_serving as cs

SIMULATED = 'SIMULATED'
NOTE = ('SIMULATED uplift: trained on simulated treatment (the data has no treatment column) under the assumptions in config.yaml. '
        'Not a finding about real retention offers.')
ROOT = Path(__file__).resolve().parent.parent
ARTIFACT = 'churn_uplift_sim.joblib'


def validate_assumptions(cfg: dict | None) -> dict:
    if not cfg:
        raise ValueError('config.yaml has no uplift_simulation block; the simulated uplift model cannot be built')
    p = cfg.get('treatment_probability')
    r = cfg.get('retention_probability_by_contract')
    if p is None or not 0 < p < 1:
        raise ValueError('uplift_simulation.treatment_probability must be between 0 and 1')
    if not r or any(v is None or not 0 <= v <= 1 for v in r.values()):
        raise ValueError('uplift_simulation.retention_probability_by_contract needs a probability in [0, 1] for every Contract')
    return cfg


def simulate_treatment(X: pd.DataFrame, y: pd.Series, cfg: dict, seed: int | None = None) -> pd.DataFrame:
    """SIMULATED treatment column and outcome.
    Assignment: treated ~ Bernoulli(treatment_probability), independent of features (randomised).
    Effect: a treated customer who would have churned is retained with probability retention_probability_by_contract[Contract]."""
    cfg = validate_assumptions(cfg)
    missing = set(X['Contract']) - set(cfg['retention_probability_by_contract'])
    if missing:
        raise ValueError('No retention_probability_by_contract for Contract values %s' % sorted(missing))
    rng = np.random.default_rng(cfg.get('seed', 42) if seed is None else seed)
    treated = rng.random(len(X)) < cfg['treatment_probability']
    retained = rng.random(len(X)) < X['Contract'].map(cfg['retention_probability_by_contract']).values
    y_sim = np.where(treated & retained, 0, y.values)
    return pd.DataFrame({'simulated_treatment': treated.astype(int), 'simulated_outcome': y_sim}, index=X.index)


def assumed_true_uplift(X: pd.DataFrame, p_churn: np.ndarray, cfg: dict) -> np.ndarray:
    """Uplift implied by the SIMULATION assumptions: P(churn|x) * retention probability of the customer's Contract."""
    return np.asarray(p_churn) * X['Contract'].map(cfg['retention_probability_by_contract']).values


def _learner(columns: list[str], meta: dict) -> Pipeline:
    cat = meta['categorical_columns']
    num = [c for c in columns if c not in cat]
    prep = ColumnTransformer([('num', 'passthrough', num), ('cat', OneHotEncoder(handle_unknown='ignore'), cat)])
    return Pipeline([('prep', prep), ('model', XGBClassifier(n_estimators=150, learning_rate=0.05, max_depth=2, subsample=0.8,
                                                              tree_method='hist', random_state=42, n_jobs=2, eval_metric='logloss'))])


def fit_t_learner(X: pd.DataFrame, sim: pd.DataFrame, meta: dict) -> dict:
    """Two outcome models, one on SIMULATED-treated and one on SIMULATED-control customers."""
    cols = meta['input_columns']
    m_t, m_c = _learner(cols, meta), _learner(cols, meta)
    t = sim['simulated_treatment'].values == 1
    m_t.fit(X.loc[t, cols], sim.loc[t, 'simulated_outcome'])
    m_c.fit(X.loc[~t, cols], sim.loc[~t, 'simulated_outcome'])
    return {'treated_model': m_t, 'control_model': m_c, 'n_treated': int(t.sum()), 'n_control': int((~t).sum())}


def score_uplift(df: pd.DataFrame, ub: dict, meta: dict) -> pd.DataFrame:
    """SIMULATED uplift = P(churn | control) - P(churn | treated) from the two models. Every row is labelled SIMULATED."""
    X = cs.prepare(df, meta)
    p_c = ub['models']['control_model'].predict_proba(X)[:, 1]
    p_t = ub['models']['treated_model'].predict_proba(X)[:, 1]
    return pd.DataFrame({'simulated_uplift': p_c - p_t, 'simulated_p_churn_control': p_c, 'simulated_p_churn_treated': p_t,
                         'label': SIMULATED, 'note': NOTE}, index=df.index)


def build(bundle: dict, csv=None, out=None, cfg: dict | None = None) -> dict:
    """Simulate treatment on the TRAIN split, fit the T-learner, and evaluate on the SIMULATED test split. Saves the artifact."""
    from app.fairness import load_test_split
    meta = bundle['meta']
    cfg = validate_assumptions(cfg if cfg is not None else (bundle.get('config') or {}).get('uplift_simulation'))
    df = pd.read_csv(csv or ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv').drop(columns=['customerID'])
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    y = df['Churn'].map({'Yes': 1, 'No': 0})
    X_tr, X_te, y_tr, y_te = train_test_split(df.drop(columns=['Churn']), y, test_size=0.2, random_state=42, stratify=y)
    sim_tr = simulate_treatment(X_tr, y_tr, cfg)
    models = fit_t_learner(X_tr, sim_tr, meta)
    ub = {'models': models, 'assumptions': cfg, 'label': SIMULATED, 'note': NOTE}
    # Sanity of the PIPELINE on the test split: does the learner recover the uplift the simulation assumed?
    p_cal = cs.predict(X_te, bundle)['churn_probability'].values
    truth = assumed_true_uplift(X_te, p_cal, cfg)
    est = score_uplift(X_te, ub, meta)['simulated_uplift'].values
    sim_te = simulate_treatment(X_te, y_te, cfg, seed=cfg.get('seed', 42) + 1)
    ub['evaluation'] = {'label': SIMULATED, 'n_train': int(len(X_tr)), 'n_treated': models['n_treated'], 'n_control': models['n_control'],
                        'sim_churn_rate_treated_train': float(sim_tr.loc[sim_tr.simulated_treatment == 1, 'simulated_outcome'].mean()),
                        'sim_churn_rate_control_train': float(sim_tr.loc[sim_tr.simulated_treatment == 0, 'simulated_outcome'].mean()),
                        'corr_estimated_vs_assumed_uplift_test': float(np.corrcoef(est, truth)[0, 1]),
                        'mean_estimated_uplift_test': float(est.mean()), 'mean_assumed_uplift_test': float(truth.mean()),
                        'sim_test_treated_share': float(sim_te.simulated_treatment.mean())}
    joblib.dump(ub, out or ROOT / ARTIFACT)
    return ub


def load(path=None) -> dict | None:
    p = Path(path or ROOT / ARTIFACT)
    return joblib.load(p) if p.exists() else None


if __name__ == '__main__':
    b = cs.load_bundle(ROOT)
    ub = build(b)
    print('*** SIMULATED uplift pipeline check (not findings) ***')
    for k, v in ub['evaluation'].items():
        print(' ', k, v)
