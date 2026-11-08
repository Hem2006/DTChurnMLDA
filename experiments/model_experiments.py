"""Offline experiments for the paper (Table III/IV/V/VIII/IX). Reads the served artifacts; never writes or replaces them.

    .venv/Scripts/python -m experiments.model_experiments            # -> experiments/results/model_experiments.json

Everything is computed on the seed-42 80/20 split that train.py uses. Model fits here (Platt / uncalibrated OOF, RF and LightGBM
5-fold searches, ablation variants) are throw-away comparison models; the served CalibratedClassifierCV is only loaded and scored.
The profit grid uses HYPOTHETICAL c, v, s values chosen only to show sensitivity; they are not business numbers.
"""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier  # noqa: F401  (imported by train.make_searches)
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, f1_score, roc_auc_score
from sklearn.model_selection import RepeatedStratifiedKFold, StratifiedKFold, cross_val_predict, cross_val_score, train_test_split
from xgboost import XGBClassifier

import churn_serving as cs
import train as T
from experiments.stats import delong_test, ece_quantile, ece_uniform, top_fraction_lift

warnings.filterwarnings('ignore')
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'experiments' / 'results'
B = 2000


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def load_split():
    df = pd.read_csv(ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv').drop(columns=['customerID'])
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    y = df['Churn'].map({'Yes': 1, 'No': 0})
    return train_test_split(df.drop(columns=['Churn']), y, test_size=0.2, random_state=T.SEED, stratify=y)


def boot_ci(fn, y, *ps, idx):
    vals = np.array([fn(y[i], *[p[i] for p in ps]) for i in idx])
    return [float(np.quantile(vals, 0.025)), float(np.quantile(vals, 0.975))]


def ranking(y, p, idx):
    return {'roc_auc': float(roc_auc_score(y, p)), 'pr_auc': float(average_precision_score(y, p)),
            'pr_auc_ci95': boot_ci(average_precision_score, y, p, idx=idx),
            'lift_top_decile': top_fraction_lift(y, p, 0.10), 'lift_top_decile_ci95': boot_ci(lambda a, b: top_fraction_lift(a, b, 0.10), y, p, idx=idx)}


def f1_threshold(y, p):
    f1s = np.array([f1_score(y, p >= t) for t in T.GRID])
    return float(T.GRID[int(np.argmax(f1s))])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads((ROOT / 'churn_model_meta.json').read_text())
    X_train, X_test, y_train, y_test = load_split()
    drop = meta['dropped_columns']
    Xtr, Xte = X_train.drop(columns=drop), X_test.drop(columns=drop)
    yt, ytr = y_test.values, y_train.values
    rng = np.random.default_rng(T.SEED)
    idx = rng.integers(0, len(yt), size=(B, len(yt)))          # same resamples as train.py's CIs
    res = {'note': 'Offline experiments; served artifacts read-only. Seed-42 80/20 split; bootstrap %d resamples (percentile CIs).' % B}

    # ---------------- served model, references, scores
    served = joblib.load(ROOT / 'churn_model.joblib')
    xgb_raw = joblib.load(ROOT / 'churn_xgb_explainer.joblib')      # = the tuned XGBoost pipeline before calibration
    lr_ref = joblib.load(ROOT / 'churn_logreg_ref.joblib')
    p_served = served.predict_proba(Xte)[:, 1]
    p_raw = xgb_raw.predict_proba(Xte)[:, 1]
    p_lr = lr_ref.predict_proba(Xte)[:, 1]
    p_base = T.make_baseline().fit(X_train, y_train).predict_proba(X_test)[:, 1]
    res['sanity'] = {'served_auc_matches_meta': abs(roc_auc_score(yt, p_served) - meta['test_metrics']['roc_auc']) < 1e-9,
                     'uncalibrated_brier_matches_meta': abs(brier_score_loss(yt, p_raw) - meta['calibration_test']['brier_uncalibrated']) < 1e-9,
                     'base_rate_test': float(yt.mean())}
    log('sanity', res['sanity'])

    # ---------------- RF and LightGBM: same 5-fold randomised search as train.py, refit on train, scored on test
    searches = T.make_searches(Xtr, meta['totalcharges_mode'], T.CV5, n_jobs=2)
    fam = {}
    for name in ('random_forest', 'lightgbm'):
        s = searches[name].fit(Xtr, y_train)
        fam[name] = s.best_estimator_.predict_proba(Xte)[:, 1]
        log(name, 'search best CV AUC %.4f' % s.best_score_, 'test AUC %.4f' % roc_auc_score(yt, fam[name]))
        res.setdefault('family_search', {})[name] = {'best_cv_auc': float(s.best_score_), 'best_params': {k: (v if not isinstance(v, np.generic) else v.item()) for k, v in s.best_params_.items()}}

    models = {'baseline_contract_tenure': p_base, 'logreg_reference': p_lr, 'random_forest': fam['random_forest'],
              'xgboost_served_calibrated': p_served, 'xgboost_uncalibrated': p_raw, 'lightgbm': fam['lightgbm']}
    res['ranking'] = {k: ranking(yt, p, idx) for k, p in models.items()}
    for k, v in res['ranking'].items():
        log('ranking', k, {a: (round(b, 4) if isinstance(b, float) else b) for a, b in v.items()})

    # ---------------- DeLong paired tests (served vs each)
    res['delong_vs_served'] = {k: delong_test(yt, p_served, p) for k, p in models.items() if k not in ('xgboost_served_calibrated',)}
    log('delong served vs LR', res['delong_vs_served']['logreg_reference'])

    # ---------------- Calibration ablation: none vs isotonic (served) vs Platt (sigmoid)
    inner = StratifiedKFold(n_splits=5, shuffle=True, random_state=T.SEED + 1)
    oof_none = cross_val_predict(clone(xgb_raw), Xtr, y_train, cv=T.CV5, method='predict_proba')[:, 1]
    oof = {'none': oof_none}
    for m in ('isotonic', 'sigmoid'):
        est = CalibratedClassifierCV(clone(xgb_raw), method=m, cv=inner, ensemble=True)
        oof[m] = cross_val_predict(est, Xtr, y_train, cv=T.CV5, method='predict_proba')[:, 1]
    platt = CalibratedClassifierCV(clone(xgb_raw), method='sigmoid', cv=inner, ensemble=True).fit(Xtr, y_train)
    p_platt = platt.predict_proba(Xte)[:, 1]
    tests = {'none': p_raw, 'isotonic': p_served, 'sigmoid': p_platt}
    cal = {}
    for m, p in tests.items():
        t = f1_threshold(ytr, oof[m])
        pt = T.point(p, t, yt)
        cal[m] = {'oof_brier_train': float(brier_score_loss(ytr, oof[m])), 'f1_optimal_threshold_oof': t,
                  'test_brier': float(brier_score_loss(yt, p)), 'test_brier_ci95': boot_ci(brier_score_loss, yt, p, idx=idx),
                  'test_ece_quantile10': ece_quantile(yt, p, 10), 'test_ece_quantile10_ci95': boot_ci(ece_quantile, yt, p, idx=idx),
                  'test_ece_uniform10': ece_uniform(yt, p, 10), 'test_ece_quantile15': ece_quantile(yt, p, 15),
                  'test_mean_score': float(p.mean()), 'test_roc_auc': float(roc_auc_score(yt, p)),
                  'test_at_own_threshold': {k: float(v) for k, v in pt.items()}}
        log('calibration', m, {k: v for k, v in cal[m].items() if not isinstance(v, (dict, list))})
    res['calibration_ablation'] = cal
    res['calibration_ablation']['note'] = ('ECE: equal-frequency (quantile) bins, 10 bins, |mean score - churn rate| weighted by bin share (as in train.py); '
                                           'equal-width-10 and quantile-15 shown as sensitivity. Threshold = F1-optimal on calibrated OOF train predictions '
                                           '(grid 0.020-0.950 step 0.005), the same rule that set the served placeholder.')

    # ---------------- Feature-set ablation (same protocol as train.py) + extra variants + test-set effect
    variants = {'A: TotalCharges blanks = median': ([], 'median'), 'B: TotalCharges blanks = 0': ([], 'zero'),
                'C: drop TotalCharges': (['TotalCharges'], 'drop'), 'D: drop TotalCharges + gender (served)': (['TotalCharges', 'gender'], 'drop'),
                'E: drop gender only (TotalCharges median)': (['gender'], 'median'),
                'F: D + drop SeniorCitizen, Partner, Dependents': (['TotalCharges', 'gender', 'SeniorCitizen', 'Partner', 'Dependents'], 'drop'),
                'G: D + drop MonthlyCharges': (['TotalCharges', 'gender', 'MonthlyCharges'], 'drop')}
    RCV = RepeatedStratifiedKFold(n_splits=5, n_repeats=2, random_state=11)
    abl = {}
    for name, (dr, mode) in variants.items():
        Xv, Xvt = X_train.drop(columns=dr), X_test.drop(columns=dr)
        lr = T.make_pipe(LogisticRegression(max_iter=5000, C=0.1, class_weight='balanced'), Xv, mode)
        xg = T.make_pipe(XGBClassifier(n_estimators=250, learning_rate=0.03, max_depth=3, subsample=0.8, colsample_bytree=0.7,
                                       tree_method='hist', random_state=T.SEED, n_jobs=2), Xv, mode, scale=False)
        cv_lr = cross_val_score(lr, Xv, y_train, cv=RCV, scoring='roc_auc').mean()
        cv_xg = cross_val_score(xg, Xv, y_train, cv=RCV, scoring='roc_auc').mean()
        t_lr = roc_auc_score(yt, clone(lr).fit(Xv, y_train).predict_proba(Xvt)[:, 1])
        t_xg = roc_auc_score(yt, clone(xg).fit(Xv, y_train).predict_proba(Xvt)[:, 1])
        abl[name] = {'cv_logreg_auc': float(cv_lr), 'cv_xgb_auc': float(cv_xg), 'cv_mean_auc': float((cv_lr + cv_xg) / 2),
                     'test_logreg_auc': float(t_lr), 'test_xgb_auc': float(t_xg), 'test_mean_auc': float((t_lr + t_xg) / 2), 'n_inputs': int(Xv.shape[1])}
        log('ablation', name, {k: round(v, 4) for k, v in abl[name].items()})
    res['feature_ablation'] = abl
    res['feature_ablation_protocol'] = ('Fixed, untuned models: logistic regression (C=0.1, balanced) and XGBoost (250 trees, lr 0.03, depth 3, '
                                        'subsample 0.8, colsample 0.7); 5-fold stratified CV repeated twice (random_state 11) on the training set; '
                                        'mean CV AUC = average of the two models. Test effect: same fixed models refit on all of train, scored once on test.')

    # ---------------- Profit sensitivity (HYPOTHETICAL grid; realised on test labels and model-expected)
    grid = []
    for c in (5, 10, 20, 50):
        for v in (200, 500, 800, 1500):
            for s in (0.1, 0.2, 0.3, 0.5):
                tstar = c / (s * v)
                row = {'c': c, 'v': v, 's': s, 't_star': tstar}
                for name, contact in (('no_action', np.zeros(len(yt), bool)), ('flag_everyone', np.ones(len(yt), bool)),
                                      ('cost_threshold', p_served >= tstar), ('placeholder_0.315', p_served >= meta['threshold'])):
                    realised = np.where(contact, yt * s * v - c, 0.0)
                    expected = np.where(contact, p_served * s * v - c, 0.0)
                    row[name] = {'contacted': int(contact.sum()), 'realised_profit_per_customer': float(realised.mean()),
                                 'expected_profit_per_customer': float(expected.mean())}
                grid.append(row)
    res['profit_grid'] = {'label': 'HYPOTHETICAL values; sensitivity analysis only, not a business result',
                          'definition': 'per test customer (n=1409): mean over customers of contact * (y*s*v - c) (realised, uses observed churn) '
                                        'and contact * (p*s*v - c) (expected, uses calibrated p). Assumes the offer only affects would-be churners.',
                          'rows': grid}
    best = pd.DataFrame([{'c': r['c'], 'v': r['v'], 's': r['s'],
                          **{k: r[k]['realised_profit_per_customer'] for k in ('no_action', 'flag_everyone', 'cost_threshold', 'placeholder_0.315')}} for r in grid])
    res['profit_grid']['summary'] = {
        'cells': len(best),
        'cost_threshold_best_or_tied_share': float((best['cost_threshold'] >= best[['no_action', 'flag_everyone', 'placeholder_0.315']].max(axis=1) - 1e-9).mean()),
        'cost_threshold_beats_placeholder_share': float((best['cost_threshold'] > best['placeholder_0.315'] + 1e-9).mean()),
        'cost_threshold_ge_flag_everyone_share': float((best['cost_threshold'] >= best['flag_everyone'] - 1e-9).mean()),
        'cost_threshold_ge_no_action_share': float((best['cost_threshold'] >= -1e-9).mean())}
    log('profit summary', res['profit_grid']['summary'])

    # ---------------- reporting facts the paper needs
    import yaml
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text())
    ub = joblib.load(ROOT / 'churn_uplift_sim.joblib') if (ROOT / 'churn_uplift_sim.joblib').exists() else {}
    res['uplift_simulation_config'] = cfg.get('uplift_simulation')
    res['uplift_simulation_evaluation'] = {k: (v if not isinstance(v, np.generic) else v.item()) for k, v in (ub.get('evaluation') or {}).items()}
    res['inner_loop'] = {'outer': '5-fold stratified (random_state 123)', 'inner': '3-fold stratified (random_state 7)',
                         'logreg': 'grid over C = logspace(-3, 1, 13), balanced class weights (13 candidates)',
                         'random_forest': 'randomised search, 8 iterations', 'xgboost': 'randomised search, 25 iterations',
                         'lightgbm': 'randomised search, 15 iterations', 'final_fit': '5-fold stratified search (random_state 42) on all of train',
                         'scoring': 'ROC AUC'}
    (OUT / 'model_experiments.json').write_text(json.dumps(res, indent=2, default=float))
    log('wrote', OUT / 'model_experiments.json')


if __name__ == '__main__':
    main()
