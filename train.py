"""train.py - rebuild every churn artifact from the Telco CSV (logic extracted from churn_pipeline.ipynb, seed 42).

Artifacts are written to a STAGING directory first. They replace the ones in the project folder only if the new main model's
test ROC AUC is within --tol (default 0.005) of the reference metrics in churn_model_meta.json; otherwise nothing is overwritten
and the script exits with status 2.

    python train.py                       # train into ./_staging, compare, promote if metrics reproduce
    python train.py --no-promote          # train and compare only
"""
import argparse
import json
import shutil
import sys
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
from sklearn.metrics import accuracy_score, brier_score_loss, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import (GridSearchCV, RandomizedSearchCV, RepeatedStratifiedKFold, StratifiedKFold,
                                     cross_val_predict, cross_val_score, train_test_split)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier

import churn_serving as cs

warnings.filterwarnings('ignore')
SEED = 42
ARTIFACTS = ['churn_model.joblib', 'churn_xgb_explainer.joblib', 'churn_logreg_ref.joblib', 'churn_model_meta.json']

CV5 = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)
CV3 = StratifiedKFold(n_splits=3, shuffle=True, random_state=7)
OUTER = StratifiedKFold(n_splits=5, shuffle=True, random_state=123)
GRID = np.arange(0.02, 0.951, 0.005)


def make_prep(Xref, tc_mode, scale=True):
    num = [c for c in Xref.select_dtypes(include='number').columns if c != 'TotalCharges']
    cat = Xref.select_dtypes(exclude='number').columns.tolist()
    sc = [('sc', StandardScaler())] if scale else []
    parts = [('num', Pipeline([('imp', SimpleImputer(strategy='median'))] + sc), num)]
    if 'TotalCharges' in Xref.columns:
        fill = SimpleImputer(strategy='constant', fill_value=0.0) if tc_mode == 'zero' else SimpleImputer(strategy='median')
        parts.append(('tc', Pipeline([('imp', fill)] + sc), ['TotalCharges']))
    parts.append(('cat', OneHotEncoder(handle_unknown='ignore'), cat))
    return ColumnTransformer(parts)


def make_pipe(model, Xref, tc_mode, scale=True):
    return Pipeline([('prep', make_prep(Xref, tc_mode, scale)), ('model', model)])


def make_baseline():
    prep = ColumnTransformer([('tenure', StandardScaler(), ['tenure']), ('contract', OneHotEncoder(handle_unknown='ignore'), ['Contract'])])
    return Pipeline([('prep', prep), ('model', LogisticRegression(max_iter=5000))])


def make_searches(Xr, tc_mode, cv, n_jobs=1):
    return {
        'logreg': GridSearchCV(make_pipe(LogisticRegression(max_iter=5000, class_weight='balanced'), Xr, tc_mode),
                               {'model__C': np.logspace(-3, 1, 13)}, scoring='roc_auc', cv=cv, n_jobs=n_jobs),
        'random_forest': RandomizedSearchCV(
            make_pipe(RandomForestClassifier(random_state=SEED, n_jobs=2, class_weight='balanced_subsample'), Xr, tc_mode, scale=False),
            {'model__n_estimators': [200, 300], 'model__max_depth': [4, 6, 8, None], 'model__min_samples_leaf': [1, 3, 5, 10, 20],
             'model__max_features': ['sqrt', 0.3, 0.5]}, n_iter=8, scoring='roc_auc', cv=cv, random_state=SEED, n_jobs=1),
        'xgboost': RandomizedSearchCV(
            make_pipe(XGBClassifier(objective='binary:logistic', eval_metric='logloss', tree_method='hist', random_state=SEED, n_jobs=2), Xr, tc_mode, scale=False),
            {'model__n_estimators': [150, 250, 400], 'model__learning_rate': [0.01, 0.02, 0.03, 0.05, 0.1],
             'model__max_depth': [2, 3, 4, 5, 6], 'model__subsample': [0.6, 0.7, 0.8, 0.9, 1.0],
             'model__colsample_bytree': [0.5, 0.7, 0.9, 1.0], 'model__min_child_weight': [1, 3, 5, 10],
             'model__gamma': [0, 0.1, 0.3, 1.0], 'model__reg_lambda': [1, 3, 10, 30],
             'model__scale_pos_weight': [1.0, 1.66, 2.76]}, n_iter=25, scoring='roc_auc', cv=cv, random_state=SEED, n_jobs=1),
        'lightgbm': RandomizedSearchCV(
            make_pipe(LGBMClassifier(random_state=SEED, n_jobs=2, verbose=-1, deterministic=True, force_row_wise=True), Xr, tc_mode, scale=False),
            {'model__n_estimators': [150, 250, 400], 'model__learning_rate': [0.02, 0.03, 0.05], 'model__num_leaves': [4, 8, 15, 31],
             'model__min_child_samples': [10, 20, 40, 80], 'model__subsample': [0.6, 0.8, 1.0], 'model__subsample_freq': [1],
             'model__colsample_bytree': [0.5, 0.7, 0.9], 'model__reg_lambda': [0, 1, 5, 20],
             'model__scale_pos_weight': [1.0, 1.66, 2.76]}, n_iter=15, scoring='roc_auc', cv=cv, random_state=SEED, n_jobs=1),
    }


def point(p, t, yv):
    pred = (p >= t).astype(int)
    return {'roc_auc': roc_auc_score(yv, p), 'brier': brier_score_loss(yv, p), 'precision': precision_score(yv, pred, zero_division=0),
            'recall': recall_score(yv, pred), 'f1': f1_score(yv, pred), 'accuracy': accuracy_score(yv, pred), 'flag_rate': pred.mean()}


def ece(yv, p, bins=10):
    edges = np.quantile(p, np.linspace(0, 1, bins + 1)); edges[-1] += 1e-9
    b = np.digitize(p, edges[1:-1])
    return float(sum(abs(p[b == k].mean() - yv[b == k].mean()) * (b == k).mean() for k in np.unique(b)))


def log(*a):
    print(*a, flush=True)


def train(data, out, config):
    out.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(data).drop(columns=['customerID'])
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    y = df['Churn'].map({'Yes': 1, 'No': 0}); assert y.notna().all()
    X = df.drop(columns=['Churn'])
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=SEED, stratify=y)
    log('train', X_train.shape, 'test', X_test.shape, '| churn rate %.3f' % y.mean())

    # Feature ablation (fixed untuned models, 5x2 CV on the training set); simplest variant within 0.001 of the best wins.
    variants = {'D: drop TotalCharges + gender': (['TotalCharges', 'gender'], 'drop'), 'C: drop TotalCharges': (['TotalCharges'], 'drop'),
                'B: TotalCharges blanks = 0': ([], 'zero'), 'A: TotalCharges blanks = median (previous)': ([], 'median')}
    RCV = RepeatedStratifiedKFold(n_splits=5, n_repeats=2, random_state=11)
    rows = {}
    for name, (drop, mode) in variants.items():
        Xv = X_train.drop(columns=drop)
        lr = make_pipe(LogisticRegression(max_iter=5000, C=0.1, class_weight='balanced'), Xv, mode)
        xg = make_pipe(XGBClassifier(n_estimators=250, learning_rate=0.03, max_depth=3, subsample=0.8, colsample_bytree=0.7,
                                     tree_method='hist', random_state=SEED, n_jobs=2), Xv, mode, scale=False)
        rows[name] = {'logreg_auc': cross_val_score(lr, Xv, y_train, cv=RCV, scoring='roc_auc').mean(),
                      'xgb_auc': cross_val_score(xg, Xv, y_train, cv=RCV, scoring='roc_auc').mean()}
        log('ablation', name, rows[name])
    abl = pd.DataFrame(rows).T; abl['mean_auc'] = abl.mean(axis=1)
    best_auc = abl['mean_auc'].max()
    chosen = next(n for n in variants if abl.loc[n, 'mean_auc'] >= best_auc - 0.001)
    DROP, TC_MODE = variants[chosen]
    X_train_f, X_test_f = X_train.drop(columns=DROP), X_test.drop(columns=DROP)
    log('chosen feature set:', chosen)

    searches = make_searches(X_train_f, TC_MODE, CV5, n_jobs=2)
    for name, s in searches.items():
        s.fit(X_train_f, y_train)
        log(f'{name:14s} 5-fold search best AUC = {s.best_score_:.4f}')

    # Nested CV (inner 3-fold search inside each of 5 outer folds) for the fair model comparison.
    nested = {}
    for name in searches:
        nested[name] = cross_val_score(make_searches(X_train_f, TC_MODE, CV3)[name], X_train_f, y_train, cv=OUTER, scoring='roc_auc', n_jobs=1)
        log('nested', name, round(nested[name].mean(), 4))
    nested['baseline (Contract + tenure)'] = cross_val_score(make_baseline(), X_train, y_train, cv=OUTER, scoring='roc_auc')
    nested_df = pd.DataFrame({k: {'mean': v.mean()} for k, v in nested.items()}).T
    fam = nested_df.drop(index='baseline (Contract + tenure)')
    tied = fam.index[fam['mean'] >= fam['mean'].max() - 0.001]
    best_name = 'xgboost' if 'xgboost' in tied else fam['mean'].idxmax()
    final_pipe = searches[best_name].best_estimator_
    log('selected:', best_name, searches[best_name].best_params_)

    RCV5 = RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=2024)
    rep = {'baseline (Contract + tenure)': cross_val_score(make_baseline(), X_train, y_train, cv=RCV5, scoring='roc_auc'),
           best_name + ' (tuned)': cross_val_score(clone(final_pipe), X_train_f, y_train, cv=RCV5, scoring='roc_auc')}

    # Calibration: isotonic vs sigmoid by out-of-fold Brier on TRAIN only; 5-fold ensemble.
    inner = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED + 1)
    oof_cal, oof_brier = {}, {}
    for m in ['isotonic', 'sigmoid']:
        est = CalibratedClassifierCV(clone(final_pipe), method=m, cv=inner, ensemble=True)
        oof_cal[m] = cross_val_predict(est, X_train_f, y_train, cv=CV5, method='predict_proba')[:, 1]
        oof_brier[m] = brier_score_loss(y_train, oof_cal[m])
    CAL_METHOD = min(oof_brier, key=oof_brier.get)
    assert CAL_METHOD == 'isotonic', 'CLAUDE.md: the served model is an isotonic CalibratedClassifierCV; got ' + CAL_METHOD
    cal_model = CalibratedClassifierCV(clone(final_pipe), method=CAL_METHOD, cv=inner, ensemble=True).fit(X_train_f, y_train)
    p_oof = oof_cal[CAL_METHOD]

    # Threshold: placeholder (F1-optimal on calibrated OOF) unless config.yaml supplies all three business numbers.
    f1s = np.array([f1_score(y_train, p_oof >= t) for t in GRID])
    placeholder_threshold = float(GRID[int(np.argmax(f1s))])
    placeholder_note = ('PLACEHOLDER. No offer cost, customer value or success rate was supplied. F1 weighs a missed churner and a wasted offer equally; '
                        'replace with offer_cost / (success_rate * customer_value) once real numbers exist.')
    threshold, threshold_source, is_placeholder = cs.get_threshold(
        {'threshold': placeholder_threshold, 'threshold_source': 'placeholder_f1_optimal_on_calibrated_oof'}, config)
    threshold_note = placeholder_note if is_placeholder else 'Derived from the business numbers supplied in config.yaml.'
    log('threshold = %.3f [%s]' % (threshold, threshold_source))

    # Test evaluation with bootstrap CIs.
    p_raw_test = final_pipe.predict_proba(X_test_f)[:, 1]
    p_test = cal_model.predict_proba(X_test_f)[:, 1]
    p_base = make_baseline().fit(X_train, y_train).predict_proba(X_test)[:, 1]
    yt = y_test.values
    pt = point(p_test, threshold, yt)
    rng = np.random.default_rng(SEED)
    B = 2000
    idx = rng.integers(0, len(yt), size=(B, len(yt)))
    boot = pd.DataFrame([point(p_test[i], threshold, yt[i]) for i in idx])
    ci = {k: [float(boot[k].quantile(0.025)), float(boot[k].quantile(0.975))] for k in boot}
    cal_stats = {'brier_uncalibrated': brier_score_loss(yt, p_raw_test), 'brier_calibrated': brier_score_loss(yt, p_test),
                 'brier_baseline_rate_only': brier_score_loss(yt, np.full(len(yt), y_train.mean())),
                 'ece_uncalibrated': ece(yt, p_raw_test), 'ece_calibrated': ece(yt, p_test),
                 'mean_score_uncalibrated': float(p_raw_test.mean()), 'mean_score_calibrated': float(p_test.mean()),
                 'observed_churn_rate_test': float(yt.mean())}
    audit = X_test[['gender', 'SeniorCitizen', 'Partner', 'Dependents']].copy(); audit['y'] = yt; audit['flag'] = (p_test >= threshold).astype(int)
    sub = pd.DataFrame([{'attribute': col, 'group': str(val), 'n': len(g), 'churn_rate': g['y'].mean(), 'flag_rate': g['flag'].mean(),
                         'recall': recall_score(g['y'], g['flag']), 'precision': precision_score(g['y'], g['flag'], zero_division=0)}
                        for col in ['gender', 'SeniorCitizen', 'Partner', 'Dependents'] for val, g in audit.groupby(col)])

    # Plain logistic-regression reference on the same 17 inputs (tuned C from the logreg search, uncalibrated). Used to sanity-check the digital twin.
    logreg_ref = searches['logreg'].best_estimator_
    p_lr = logreg_ref.predict_proba(X_test_f)[:, 1]
    logreg_ref_info = {'test_roc_auc': float(roc_auc_score(yt, p_lr)), 'best_C': float(searches['logreg'].best_params_['model__C']),
                       'class_weight': 'balanced', 'calibrated': False, 'input_columns': X_train_f.columns.tolist()}

    explainer_pipe = searches['xgboost'].best_estimator_
    meta = {
        'selected_model': best_name, 'calibration_method': CAL_METHOD, 'seed': SEED,
        'threshold': threshold, 'threshold_source': threshold_source, 'threshold_note': threshold_note,
        'input_columns': X_train_f.columns.tolist(), 'dropped_columns': DROP, 'feature_set_chosen': chosen, 'totalcharges_mode': TC_MODE,
        'numeric_columns': [c for c in X_train_f.columns if c in X_train_f.select_dtypes(include='number').columns],
        'categorical_columns': X_train_f.select_dtypes(exclude='number').columns.tolist(),
        'categorical_levels': {c: sorted(X_train_f[c].unique().tolist()) for c in X_train_f.select_dtypes(exclude='number').columns},
        'explainer_model': 'xgboost',
        'versions': cs.installed_versions(),
        'test_metrics': {k: float(v) for k, v in pt.items()}, 'test_metrics_ci95': ci, 'bootstrap_resamples': B,
        'test_auc_baseline_contract_tenure': float(roc_auc_score(yt, p_base)),
        'calibration_test': cal_stats, 'ablation_mean_auc': abl['mean_auc'].to_dict(),
        'nested_cv_auc': {k: {'mean': float(v.mean()), 'sd': float(v.std(ddof=1))} for k, v in nested.items()},
        'repeated_cv_auc': {k: {'mean': float(v.mean()), 'sd': float(v.std(ddof=1))} for k, v in rep.items()},
        'subgroups': sub.to_dict(orient='records'),
        'logreg_reference': logreg_ref_info,
    }
    joblib.dump(cal_model, out / 'churn_model.joblib')
    joblib.dump(explainer_pipe, out / 'churn_xgb_explainer.joblib')
    joblib.dump(logreg_ref, out / 'churn_logreg_ref.joblib')
    (out / 'churn_model_meta.json').write_text(json.dumps(meta, indent=2, default=float))

    # Measured explainer agreement (replaces the old name-check flag): served-model (5-pipeline average) SHAP top-3 vs the single explainer's top-3.
    bundle = cs.load_bundle(out)
    sample = X_test.iloc[:300]
    ens, _, _ = cs.explain(sample, bundle)
    single, _, _ = cs.explain_single_pipeline(sample, bundle)
    ov = cs.top_k_overlap(ens, single, k=3)
    meta['explainer_top3_overlap'] = {'k': 3, 'n_rows': int(len(ov)), 'mean_overlap': float(ov.mean()),
                                      'share_rows_with_ge_2_of_3': float((ov >= 2 / 3 - 1e-9).mean()),
                                      'definition': 'mean over rows of |top3(5-pipeline averaged SHAP) & top3(single explainer SHAP)| / 3'}
    (out / 'churn_model_meta.json').write_text(json.dumps(meta, indent=2, default=float))
    log('explainer top-3 overlap:', meta['explainer_top3_overlap'])
    return meta


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='WA_Fn-UseC_-Telco-Customer-Churn.csv')
    ap.add_argument('--staging', default='_staging')
    ap.add_argument('--reference', default='churn_model_meta.json', help='meta whose test AUC the retrain must reproduce')
    ap.add_argument('--config', default='config.yaml')
    ap.add_argument('--tol', type=float, default=0.005)
    ap.add_argument('--no-promote', action='store_true')
    ap.add_argument('--no-mlflow', action='store_true', help='skip MLflow logging')
    a = ap.parse_args(argv)
    ref = json.loads(Path(a.reference).read_text())['test_metrics']['roc_auc']
    meta = train(Path(a.data), Path(a.staging), cs.load_config(a.config))
    new = meta['test_metrics']['roc_auc']
    log('test AUC: reference %.4f, retrained %.4f, diff %.4f (tol %.3f)' % (ref, new, new - ref, a.tol))
    reproduced = abs(new - ref) <= a.tol
    if not a.no_mlflow:                                      # logged for every run, promoted or not
        from app import tracking
        rid = tracking.log_run(meta, a.staging, tags={'reference_auc': '%.4f' % ref, 'auc_diff_vs_reference': '%.4f' % (new - ref),
                                                      'reproduced_within_tol': str(reproduced).lower(),
                                                      'promoted': str(reproduced and not a.no_promote).lower()})
        log('MLflow run', rid, 'logged to', tracking.default_uri())
    if not reproduced:
        log('METRICS DO NOT REPRODUCE - nothing was overwritten. Retrained artifacts are in', a.staging)
        return 2
    if a.no_promote:
        log('metrics reproduce; --no-promote given, artifacts left in', a.staging)
        return 0
    legacy = Path('legacy_artifacts'); legacy.mkdir(exist_ok=True)
    for f in ARTIFACTS:
        if Path(f).exists() and not (legacy / f).exists():
            shutil.copy2(f, legacy / f)                      # keep the originals once
        shutil.copy2(Path(a.staging) / f, f)
    log('metrics reproduce; artifacts promoted (originals kept in legacy_artifacts/)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
