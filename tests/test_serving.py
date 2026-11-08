import warnings

import numpy as np
import pytest

import churn_serving as cs
from conftest import ROOT


def test_load_bundle_no_version_warning():
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        cs.load_bundle(ROOT)


def test_predict_matches_model(bundle, test_frame):
    X, _ = test_frame
    out = cs.predict(X, bundle)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        p = bundle['model'].predict_proba(cs.prepare(X, bundle['meta']))[:, 1]
    assert np.allclose(out['churn_probability'], p, atol=1e-12)


@pytest.mark.parametrize('mutate', [
    lambda r: r.drop(columns=['Contract']),
    lambda r: r.assign(tenure=['abc', 5, 7]),
    lambda r: r.assign(MonthlyCharges=[-1.0, 50, 60]),
    lambda r: r.assign(SeniorCitizen=[2, 0, 1]),
    lambda r: r.assign(Contract=[None, 'Two year', 'One year']),
    lambda r: r.assign(InternetService=[1, 2, 3]),
])
def test_bad_input_rejected(bundle, test_frame, mutate):
    X, _ = test_frame
    with pytest.raises(ValueError):
        cs.prepare(mutate(X.iloc[:3].copy()), bundle['meta'])


def test_unseen_category_warns_only(bundle, test_frame):
    X, _ = test_frame
    row = X.iloc[:3].copy()
    row['PaymentMethod'] = 'Crypto'
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        assert np.isfinite(cs.predict(row, bundle)['churn_probability']).all()
    assert any('unseen' in str(x.message) for x in w)
