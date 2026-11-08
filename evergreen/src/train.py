"""Phase 1: train, compare, calibrate and evaluate. Run from the evergreen/ folder:

    python -m src.train [--quick] [--no-mlflow] [--out models]

Everything is fit on the training split only; the held-out test split is used once, at the end.
--quick shrinks the randomized searches (for smoke tests); the default is the full run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.model_selection import GridSearchCV, RandomizedSearchCV, StratifiedKFold, cross_val_predict, cross_val_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

from src import data as D
from src import metrics as M
from src import threshold as T

warnings.filterwarnings('ignore')
SEED = D.SEED
CV5 = StratifiedKFold(5, shuffle=True, random_state=SEED)
CV3 = StratifiedKFold(3, shuffle=True, random_state=7)
OUTER = StratifiedKFold(5, shuffle=True, random_state=123)
FAMILIES = ['logreg', 'random_forest', 'xgboost', 'lightgbm']
BASELINE = 'baseline (Contract + tenure)'
N_JOBS = 6


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


def make_prep(scale: bool = True):
    num = Pipeline([('imp', SimpleImputer(strategy='median'))] + ([('sc', StandardScaler())] if scale else []))
    return ColumnTransformer([('num', num, D.NUMERIC), ('cat', OneHotEncoder(handle_unknown='ignore', sparse_output=False), D.CATEGORICAL)])


def make_pipe(model, scale: bool = True):
    return Pipeline([('prep', make_prep(scale)), ('model', model)])


def make_baseline():
    prep = ColumnTransformer([('tenure', StandardScaler(), ['tenure']), ('contract', OneHotEncoder(handle_unknown='ignore'), ['Contract'])])
    return Pipeline([('prep', prep), ('model', LogisticRegression(max_iter=5000))])


def make_searches(cv, quick: bool = False, n_jobs: int = N_JOBS):
    q = (lambda n: max(2, n // 6)) if quick else (lambda n: n)
    return {
        'logreg': GridSearchCV(make_pipe(LogisticRegression(max_iter=5000, class_weight='balanced')),
                               {'model__C': np.logspace(-3, 1, 4 if quick else 13)}, scoring='roc_auc', cv=cv, n_jobs=n_jobs),
        'random_forest': RandomizedSearchCV(
            make_pipe(RandomForestClassifier(random_state=SEED, n_jobs=2, class_weight='balanced_subsample'), scale=False),
            {'model__n_estimators': [200, 300], 'model__max_depth': [4, 6, 8, None], 'model__min_samples_leaf': [1, 3, 5, 10, 20],
             'model__max_features': ['sqrt', 0.3, 0.5]}, n_iter=q(8), scoring='roc_auc', cv=cv, random_state=SEED, n_jobs=n_jobs),
        'xgboost': RandomizedSearchCV(
            make_pipe(XGBClassifier(objective='binary:logistic', eval_metric='logloss', tree_method='hist', random_state=SEED, n_jobs=2), scale=False),
            {'model__n_estimators': [150, 250, 400], 'model__learning_rate': [0.01, 0.02, 0.03, 0.05, 0.1], 'model__max_depth': [2, 3, 4, 5, 6],
             'model__subsample': [0.6, 0.7, 0.8, 0.9, 1.0], 'model__colsample_bytree': [0.5, 0.7, 0.9, 1.0], 'model__min_child_weight': [1, 3, 5, 10],
             'model__gamma': [0, 0.1, 0.3, 1.0], 'model__reg_lambda': [1, 3, 10, 30]},
            n_iter=q(25), scoring='roc_auc', cv=cv, random_state=SEED, n_jobs=n_jobs),
        'lightgbm': RandomizedSearchCV(
            make_pipe(LGBMClassifier(random_state=SEED, n_jobs=2, verbose=-1, deterministic=True, force_row_wise=True), scale=False),
            {'model__n_estimators': [150, 250, 400], 'model__learning_rate': [0.02, 0.03, 0.05], 'model__num_leaves': [4, 8, 15, 31],
             'model__min_child_samples': [10, 20, 40, 80], 'model__subsample': [0.6, 0.8, 1.0], 'model__subsample_freq': [1],
             'model__colsample_bytree': [0.5, 0.7, 0.9], 'model__reg_lambda': [0, 1, 5, 20]},
            n_iter=q(15), scoring='roc_auc', cv=cv, random_state=SEED, n_jobs=n_jobs),
    }


def train(out: Path, quick: bool = False, boot: int = 2000, data_path=D.DATA) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    tr, te = D.split(D.load_clean(data_path))
    Xtr, ytr, Xte, yte = tr[D.INPUTS], tr['y'].values, te[D.INPUTS], te['y'].values
    log('train %s test %s churn rate %.3f (train %.3f, test %.3f)' % (tr.shape, te.shape, pd.concat([tr, te])['y'].mean(), ytr.mean(), yte.mean()))

    # 1. tune every family on the training split
    searches = make_searches(CV5, quick)
    for n, s in searches.items():
        s.fit(Xtr, ytr)
        log('%-13s 5-fold search best AUC %.4f %s' % (n, s.best_score_, s.best_params_))

    # 2. nested CV (5 outer folds, randomized/grid search in a 3-fold inner loop) on the training split
    nested = {}
    for n in FAMILIES:
        nested[n] = cross_val_score(make_searches(CV3, quick, n_jobs=2)[n], Xtr, ytr, cv=OUTER, scoring='roc_auc', n_jobs=3)
        log('nested', n, round(nested[n].mean(), 4), np.round(nested[n], 4))
    nested[BASELINE] = cross_val_score(make_baseline(), Xtr, ytr, cv=OUTER, scoring='roc_auc')
    log('nested', BASELINE, round(nested[BASELINE].mean(), 4))

    # 3. calibration method by out-of-fold Brier on the TRAINING split only
    xgb_pipe = searches['xgboost'].best_estimator_
    inner = StratifiedKFold(5, shuffle=True, random_state=SEED + 1)
    oof, oof_brier = {}, {}
    for m in ['isotonic', 'sigmoid']:
        est = CalibratedClassifierCV(clone(xgb_pipe), method=m, cv=inner, ensemble=True)
        oof[m] = cross_val_predict(est, Xtr, ytr, cv=CV5, method='predict_proba', n_jobs=5)[:, 1]
        oof_brier[m] = float(brier_score_loss(ytr, oof[m]))
    method = min(oof_brier, key=oof_brier.get)
    log('OOF Brier', oof_brier, '-> serving', method)
    cal = {m: CalibratedClassifierCV(clone(xgb_pipe), method=m, cv=inner, ensemble=True).fit(Xtr, ytr) for m in ['isotonic', 'sigmoid']}
    served = cal[method]
    placeholder = T.f1_optimal(ytr, oof[method])

    # 4. one-shot test evaluation
    thr = T.resolve(T.load_business(D.ROOT / 'config' / 'business.yaml'), placeholder)
    p_unc = xgb_pipe.predict_proba(Xte)[:, 1]
    p_platt, p_iso = cal['sigmoid'].predict_proba(Xte)[:, 1], cal['isotonic'].predict_proba(Xte)[:, 1]
    p_served = p_iso if method == 'isotonic' else p_platt
    preds = {n: searches[n].best_estimator_.predict_proba(Xte)[:, 1] for n in FAMILIES}      # xgboost here = tuned, uncalibrated
    preds[BASELINE] = make_baseline().fit(Xtr, ytr).predict_proba(Xte)[:, 1]
    logreg_ref = searches['logreg'].best_estimator_

    point, ci = M.bootstrap(yte, p_served, lambda y, p: M.point_metrics(y, p, thr['threshold']), B=boot)
    families = {}
    for n, p in preds.items():
        pt, c = M.bootstrap(yte, p, lambda y, q: {'roc_auc': roc_auc_score(y, q)}, B=boot)
        families[n] = {'test_roc_auc': pt['roc_auc'], 'test_roc_auc_ci95': c['roc_auc']}
    for n in FAMILIES:
        families[n].update(nested_cv_auc_mean=float(nested[n].mean()), nested_cv_auc_sd=float(nested[n].std(ddof=1)), nested_cv_folds=nested[n].tolist(),
                           best_params={k.replace('model__', ''): (v.item() if hasattr(v, 'item') else v) for k, v in searches[n].best_params_.items()})
    families[BASELINE].update(nested_cv_auc_mean=float(nested[BASELINE].mean()), nested_cv_auc_sd=float(nested[BASELINE].std(ddof=1)),
                              nested_cv_folds=nested[BASELINE].tolist())
    # paired bootstrap of the AUC difference served - logistic
    rng, p_lr = np.random.default_rng(SEED), preds['logreg']
    d = []
    for _ in range(boot):
        i = rng.integers(0, len(yte), len(yte))
        if yte[i].min() != yte[i].max():
            d.append(roc_auc_score(yte[i], p_served[i]) - roc_auc_score(yte[i], p_lr[i]))
    calib = {name: {'brier': float(brier_score_loss(yte, p)), 'log_loss': float(log_loss(yte, np.clip(p, 1e-6, 1 - 1e-6))), 'ece': M.ece(yte, p),
                    'mean_prediction': float(p.mean()), 'roc_auc': float(roc_auc_score(yte, p))}
             for name, p in [('uncalibrated', p_unc), ('platt', p_platt), ('isotonic', p_iso)]}
    metrics = {
        'seed': SEED, 'n_train': len(tr), 'n_test': len(te), 'churn_rate_test': float(yte.mean()), 'inputs': D.INPUTS,
        'served': {'model': 'xgboost', 'calibration': method, 'oof_brier_train': oof_brier},
        'threshold': thr, 'test': point, 'test_ci95': ci, 'bootstrap_resamples': boot, 'ece_binning': M.ECE_BINNING,
        'brier_base_rate_predictor': float(brier_score_loss(yte, np.full(len(yte), ytr.mean()))),
        'calibration_comparison': calib, 'families': families,
        'delong_served_vs_logreg': M.delong_test(yte, p_served, p_lr),
        'auc_diff_served_minus_logreg_ci95': [float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))],
    }
    meta = {'inputs': D.INPUTS, 'numeric': D.NUMERIC, 'categorical': D.CATEGORICAL, 'levels': D.levels(tr), 'calibration': method,
            'placeholder_threshold': placeholder, 'placeholder_source': T.PLACEHOLDER_SOURCE, 'seed': SEED, 'versions': versions(),
            'train_reference': {'tenure': tr['tenure'].tolist(), 'MonthlyCharges': tr['MonthlyCharges'].tolist(), 'Contract': tr['Contract'].tolist()}}

    joblib.dump(served, out / 'served_model.joblib')
    joblib.dump(cal, out / 'calibrated_variants.joblib')
    joblib.dump(xgb_pipe, out / 'xgb_uncalibrated.joblib')
    joblib.dump(logreg_ref, out / 'logreg_ref.joblib')
    (out / 'meta.json').write_text(json.dumps(meta, default=float))
    (out / 'metrics.json').write_text(json.dumps(metrics, indent=2, default=float))
    tp = te[['customerID', 'gender'] + D.INPUTS + ['y']].copy()
    for n, p in preds.items():
        tp['p_' + n] = p
    tp['p_xgb_platt'], tp['p_xgb_isotonic'] = p_platt, p_iso
    tp['p_served'] = p_served
    tp.to_csv(out / 'test_predictions.csv', index=False)
    log('served', method, 'test AUC %.4f [%.4f, %.4f]' % (point['roc_auc'], *ci['roc_auc']), 'threshold %.3f (%s)' % (thr['threshold'], thr['source']))
    return metrics


def versions():
    import sklearn, xgboost, lightgbm, shap, scipy
    return {'scikit-learn': sklearn.__version__, 'xgboost': xgboost.__version__, 'lightgbm': lightgbm.__version__, 'shap': shap.__version__,
            'pandas': pd.__version__, 'numpy': np.__version__, 'scipy': scipy.__version__}


def log_mlflow(metrics: dict, out: Path):
    import mlflow
    mlflow.set_tracking_uri('sqlite:///' + (D.ROOT / 'mlflow.db').as_posix())
    mlflow.set_experiment('evergreen')
    with mlflow.start_run(run_name='train'):
        mlflow.log_params({'served_model': 'xgboost', 'calibration': metrics['served']['calibration'], 'seed': SEED,
                           'threshold': metrics['threshold']['threshold'], 'threshold_source': metrics['threshold']['source']})
        for k, v in metrics['test'].items():
            mlflow.log_metric('test_' + k, v)
        for n, f in metrics['families'].items():
            mlflow.log_metric('test_auc_' + n.split(' ')[0], f['test_roc_auc'])
            if 'nested_cv_auc_mean' in f:
                mlflow.log_metric('nested_auc_' + n.split(' ')[0], f['nested_cv_auc_mean'])
        mlflow.log_artifact(str(out / 'metrics.json'))
        mlflow.log_artifact(str(out / 'meta.json'))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(D.ROOT / 'models'))
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--boot', type=int, default=2000)
    ap.add_argument('--no-mlflow', action='store_true')
    a = ap.parse_args(argv)
    out = Path(a.out)
    m = train(out, a.quick, a.boot)
    if not a.no_mlflow:
        try:
            log_mlflow(m, out)
            log('logged to MLflow (mlflow.db)')
        except Exception as e:                      # tracking must never lose a trained model
            log('MLflow logging failed:', e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
