import numpy as np
import pandas as pd

import churn_serving as cs

MIN_OVERLAP = 0.70


def test_additivity_every_base_pipeline(bundle, test_frame):
    X, _ = test_frame
    parts = cs.explain_per_pipeline(X.iloc[:300], bundle)
    assert len(parts) == 5
    for contrib, base, margin in parts:
        assert (contrib.sum(axis=1) + base - margin).abs().max() < 1e-4


def test_averaged_additivity_and_columns(bundle, test_frame):
    X, _ = test_frame
    contrib, base, margin = cs.explain(X.iloc[:300], bundle)
    assert (contrib.sum(axis=1) + base - margin).abs().max() < 1e-4
    assert list(contrib.columns) == bundle['meta']['input_columns']


def test_explains_served_ensemble_not_legacy_pipeline(bundle, test_frame):
    X, _ = test_frame
    assert len(bundle['model'].calibrated_classifiers_) == 5
    ens, _, _ = cs.explain(X.iloc[:50], bundle)
    single, _, _ = cs.explain_single_pipeline(X.iloc[:50], bundle)
    assert not np.allclose(ens.values, single.reindex(columns=ens.columns, fill_value=0).values)


def test_top3_overlap_with_legacy_explainer(bundle, test_frame):
    X, _ = test_frame
    sample = X.iloc[:300]
    ens, _, _ = cs.explain(sample, bundle)
    single, _, _ = cs.explain_single_pipeline(sample, bundle)
    ov = cs.top_k_overlap(ens, single, k=3)
    print('\nMEASURED top-3 SHAP overlap (n=%d): mean %.3f, rows with >=2/3 shared %.3f' % (len(ov), ov.mean(), (ov >= 2 / 3 - 1e-9).mean()))
    assert len(ov) == 300 and ov.mean() >= MIN_OVERLAP


def test_meta_records_measured_overlap_not_name_check(bundle):
    meta = bundle['meta']
    assert 'explainer_matches_served_model' not in meta
    ov = meta['explainer_top3_overlap']
    assert ov['n_rows'] == 300 and ov['k'] == 3 and ov['mean_overlap'] >= MIN_OVERLAP


def test_explainers_cached_once_per_bundle(bundle, test_frame):
    X, _ = test_frame
    cs.explain(X.iloc[:5], bundle)
    first = bundle['_explainers']
    cs.explain(X.iloc[5:10], bundle)
    assert bundle['_explainers'] is first


def test_top_reasons_only_flagged_with_probability(bundle, test_frame):
    X, _ = test_frame
    sample = X.iloc[:200]
    pred = cs.predict(sample, bundle)
    reasons = cs.top_reasons(sample, bundle, k=3)
    assert set(reasons.index) == set(pred.index[pred['flagged']])
    assert 0 < len(reasons) < len(sample)
    assert np.allclose(reasons['churn_probability'], pred.loc[reasons.index, 'churn_probability'])
    for r in reasons['reasons']:
        assert 1 <= len(r) <= 3 and all(d['log_odds_push'] > 0 for d in r)


def test_top_reasons_empty_when_nobody_flagged(bundle, test_frame):
    X, _ = test_frame
    b = dict(bundle, config={'offer_cost': 999, 'customer_value': 100, 'success_rate': 1.0})   # threshold 9.99: nobody flagged
    out = cs.top_reasons(X.iloc[:20], b)
    assert isinstance(out, pd.DataFrame) and len(out) == 0 and list(out.columns) == ['churn_probability', 'reasons']
