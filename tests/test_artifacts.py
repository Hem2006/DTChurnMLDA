import json

import joblib
import pytest
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import roc_auc_score

import churn_serving as cs
from conftest import ROOT


def test_served_model_is_isotonic_5fold_calibrated_ensemble(bundle):
    m = bundle['model']
    assert isinstance(m, CalibratedClassifierCV) and m.method == 'isotonic' and len(m.calibrated_classifiers_) == 5
    assert bundle['meta']['calibration_method'] == 'isotonic'


def test_saved_metrics_match_recomputed_test_auc(bundle, test_frame):
    X, y = test_frame
    p = cs.predict(X, bundle)['churn_probability']
    assert abs(roc_auc_score(y, p) - bundle['meta']['test_metrics']['roc_auc']) < 1e-6


def test_logreg_reference_model(bundle, test_frame):
    X, y = test_frame
    ref = joblib.load(ROOT / 'churn_logreg_ref.joblib')
    cols = bundle['meta']['input_columns']
    assert len(cols) == 17 and bundle['meta']['logreg_reference']['input_columns'] == cols
    auc = roc_auc_score(y, ref.predict_proba(X[cols])[:, 1])
    assert auc == pytest.approx(bundle['meta']['logreg_reference']['test_roc_auc'], abs=1e-9)
    assert 0.80 < auc < 0.88


def test_pinned_versions_match_meta():
    meta = json.loads((ROOT / 'churn_model_meta.json').read_text())
    pins = dict(l.strip().split('==') for l in (ROOT / 'requirements.txt').read_text().split('\n') if '==' in l)
    for lib in ['scikit-learn', 'xgboost', 'lightgbm', 'shap', 'pandas', 'numpy', 'scipy', 'joblib']:
        assert pins[lib] == meta['versions'][lib]
    assert not cs.check_versions(meta)
