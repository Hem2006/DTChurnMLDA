"""SIMULATED uplift: a T-learner (two XGBoost models) trained on SIMULATED treatment. ADVISORY ONLY.

The Telco data has no treatment column, so treatment and its effect are simulated from the assumptions in config/uplift_sim.yaml.
Everything returned here is labelled SIMULATED; the numbers show the pipeline works, they are not findings about real retention offers.

    python -m src.uplift        # build models/uplift.joblib
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier

from src import data as D
from src import serving as S

SIMULATED = 'SIMULATED'
NOTE = ('SIMULATED uplift: trained on simulated treatment (the data has no treatment column) under assumptions in config/uplift_sim.yaml. '
        'Advisory only. Not a finding about real retention offers.')
QUADRANTS = ('persuadable', 'sure thing', 'lost cause', 'sleeping dog')


def validate_assumptions(cfg: dict) -> dict:
    p, r = cfg.get('treatment_probability'), cfg.get('retention_probability_by_contract')
    if p is None or not 0 < p < 1:
        raise ValueError('uplift_sim.yaml: treatment_probability must be in (0, 1)')
    if not r or any(v is None or not 0 <= v <= 1 for v in r.values()):
        raise ValueError('uplift_sim.yaml: retention_probability_by_contract needs a probability in [0, 1] for every Contract')
    return cfg


def simulate_treatment(X: pd.DataFrame, y: np.ndarray, cfg: dict, seed: int | None = None) -> pd.DataFrame:
    """SIMULATED assignment (Bernoulli, independent of features) and outcome (a treated would-be churner is retained with the contract's probability)."""
    cfg = validate_assumptions(cfg)
    missing = set(X['Contract']) - set(cfg['retention_probability_by_contract'])
    if missing:
        raise ValueError('no retention probability for Contract values %s' % sorted(missing))
    rng = np.random.default_rng(cfg.get('seed', 42) if seed is None else seed)
    treated = rng.random(len(X)) < cfg['treatment_probability']
    retained = rng.random(len(X)) < X['Contract'].map(cfg['retention_probability_by_contract']).values
    return pd.DataFrame({'simulated_treatment': treated.astype(int), 'simulated_outcome': np.where(treated & retained, 0, y)}, index=X.index)


def _learner() -> Pipeline:
    prep = ColumnTransformer([('num', 'passthrough', D.NUMERIC), ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), D.CATEGORICAL)])
    return Pipeline([('prep', prep), ('model', XGBClassifier(n_estimators=150, learning_rate=0.05, max_depth=2, subsample=0.8, tree_method='hist',
                                                              random_state=42, n_jobs=2, eval_metric='logloss'))])


def build(bundle: dict, out: Path | None = None) -> dict:
    cfg = validate_assumptions(S.load_yaml(bundle['config_dir'] / 'uplift_sim.yaml'))
    tr, te = D.split()
    sim = simulate_treatment(tr[D.INPUTS], tr['y'].values, cfg)
    t = sim['simulated_treatment'].values == 1
    m_t, m_c = _learner().fit(tr.loc[t, D.INPUTS], sim.loc[t, 'simulated_outcome']), _learner().fit(tr.loc[~t, D.INPUTS], sim.loc[~t, 'simulated_outcome'])
    ub = {'treated_model': m_t, 'control_model': m_c, 'assumptions': cfg, 'label': SIMULATED, 'note': NOTE}
    est = score(te[D.INPUTS], ub)['simulated_uplift'].values
    p = S.predict(te[D.INPUTS], bundle)['churn_probability'].values
    truth = p * te['Contract'].map(cfg['retention_probability_by_contract']).values
    ub['evaluation'] = {'label': SIMULATED, 'n_treated': int(t.sum()), 'n_control': int((~t).sum()),
                        'corr_estimated_vs_assumed_uplift_test': float(np.corrcoef(est, truth)[0, 1]),
                        'mean_estimated_uplift_test': float(est.mean()), 'mean_assumed_uplift_test': float(truth.mean())}
    joblib.dump(ub, out or bundle['models_dir'] / 'uplift.joblib')
    return ub


def score(df: pd.DataFrame, ub: dict, meta: dict | None = None) -> pd.DataFrame:
    """SIMULATED uplift = P(churn | control) - P(churn | treated)."""
    X = df[D.INPUTS] if meta is None else S.prepare(df, meta)
    pc, pt = ub['control_model'].predict_proba(X)[:, 1], ub['treated_model'].predict_proba(X)[:, 1]
    return pd.DataFrame({'simulated_uplift': pc - pt, 'simulated_p_churn_control': pc, 'simulated_p_churn_treated': pt, 'label': SIMULATED}, index=df.index)


def quadrant(uplift, p_control, risk_cut: float, min_uplift: float):
    """Four-way split of SIMULATED results. x = baseline (control) churn risk, y = simulated uplift.
    sleeping dog: offer would raise churn (uplift < 0); persuadable: high risk and uplift >= min_uplift;
    lost cause: high risk, little uplift; sure thing: low risk, little uplift."""
    uplift, p_control = np.asarray(uplift), np.asarray(p_control)
    return np.where(uplift < 0, 'sleeping dog', np.where(p_control >= risk_cut, np.where(uplift >= min_uplift, 'persuadable', 'lost cause'), 'sure thing'))


if __name__ == '__main__':
    b = S.load_bundle()
    ub = build(b)
    print('*** SIMULATED uplift pipeline check (not findings) ***')
    for k, v in ub['evaluation'].items():
        print(' ', k, v)
