"""SHAP reasons for the SERVED model, in plain English.

The served model is a 5-fold calibrated ensemble; each of its 5 base pipelines gets a TreeExplainer and the SHAP values are averaged
per raw input column. Units are log-odds of the uncalibrated XGBoost score (the isotonic/Platt map sits on top and is monotone).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src import serving as S


def _raw_name(feature: str, columns) -> str:
    body = feature.split('__', 1)[1]
    if feature.startswith('cat__'):
        return max((c for c in columns if body.startswith(c + '_')), key=len)
    return body


def _explainers(bundle: dict):
    if '_explainers' not in bundle:
        import shap
        pipes = [cc.estimator for cc in bundle['model'].calibrated_classifiers_]
        bundle['_explainers'] = [(p, shap.TreeExplainer(p.named_steps['model'])) for p in pipes]
    return bundle['_explainers']


def contributions(df: pd.DataFrame, bundle: dict) -> pd.DataFrame:
    """Per-customer SHAP by raw input column, averaged over the ensemble's base pipelines."""
    X = S.prepare(df, bundle['meta'])
    cols = bundle['meta']['inputs']
    total = None
    exps = _explainers(bundle)
    for pipe, ex in exps:
        Xt = pipe.named_steps['prep'].transform(X)
        sv = ex.shap_values(Xt)
        names = pipe.named_steps['prep'].get_feature_names_out()
        raw = [_raw_name(n, cols) for n in names]
        c = pd.DataFrame(sv, columns=names, index=X.index).T.groupby(raw).sum().T.reindex(columns=cols, fill_value=0.0)
        total = c if total is None else total + c
    return total / len(exps)


def phrase(feature: str, value) -> str:
    """One reason in plain English (describes what the model leans on; it is not advice and not causal)."""
    v = value
    simple = {
        'Contract': lambda: 'is on a %s contract' % str(v).lower(),
        'tenure': lambda: 'has been a customer for %d month%s' % (int(v), '' if int(v) == 1 else 's'),
        'MonthlyCharges': lambda: 'pays %.0f per month' % float(v),
        'PaymentMethod': lambda: 'pays by %s' % str(v).lower(),
        'PaperlessBilling': lambda: 'uses paperless billing' if v == 'Yes' else 'receives paper bills',
        'InternetService': lambda: 'has %s internet' % ('no' if v == 'No' else str(v)),
        'PhoneService': lambda: 'has phone service' if v == 'Yes' else 'has no phone service',
        'MultipleLines': lambda: {'Yes': 'has multiple phone lines', 'No': 'has a single phone line'}.get(v, 'has no phone line'),
        'SeniorCitizen': lambda: 'is a senior citizen' if v in (1, 1.0) else 'is not a senior citizen',
        'Partner': lambda: 'has a partner' if v == 'Yes' else 'has no partner',
        'Dependents': lambda: 'has dependents' if v == 'Yes' else 'has no dependents',
    }
    if feature in simple:
        return simple[feature]()
    label = {'OnlineSecurity': 'online security', 'OnlineBackup': 'online backup', 'DeviceProtection': 'device protection',
             'TechSupport': 'tech support', 'StreamingTV': 'streaming TV', 'StreamingMovies': 'streaming movies'}[feature]
    return 'has no internet service' if v == S.NO_INTERNET else ('has %s' % label if v == 'Yes' else 'does not have %s' % label)


def top_reasons(df: pd.DataFrame, bundle: dict, k: int = 3) -> list[list[dict]]:
    """For each customer, the k inputs pushing the score UP most (positive SHAP only): feature, value, push (log-odds), text."""
    X = S.prepare(df, bundle['meta'])
    c = contributions(df, bundle)
    out = []
    for i in c.index:
        top = c.loc[i].sort_values(ascending=False).head(k)
        out.append([{'feature': f, 'value': X.loc[i, f], 'push_log_odds': round(float(v), 3), 'text': phrase(f, X.loc[i, f])}
                    for f, v in top.items() if v > 0])
    return out


def sentence(reasons: list[dict]) -> str:
    if not reasons:
        return 'No single input pushes the score up.'
    texts = [r['text'] for r in reasons]
    return 'Customer ' + (texts[0] if len(texts) == 1 else ', '.join(texts[:-1]) + ' and ' + texts[-1]) + '.'
