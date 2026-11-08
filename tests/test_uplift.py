import numpy as np
import pandas as pd
import pytest

import churn_serving as cs
from app import uplift as up
from conftest import ROOT


@pytest.fixture(scope='module')
def cfg(bundle):
    return bundle['config']['uplift_simulation']


@pytest.fixture(scope='module')
def ub(bundle, tmp_path_factory):
    return up.build(bundle, out=tmp_path_factory.mktemp('u') / 'u.joblib')


def test_assumptions_are_in_config_and_documented(cfg):
    text = (ROOT / 'config.yaml').read_text()
    flat = ' '.join(text.replace('#', ' ').split())
    assert 'SIMULATED' in text and 'NOT findings' in flat
    assert 0 < cfg['treatment_probability'] < 1 and set(cfg['retention_probability_by_contract']) == {'Month-to-month', 'One year', 'Two year'}


def test_missing_or_bad_assumptions_rejected():
    with pytest.raises(ValueError, match='uplift_simulation'):
        up.validate_assumptions(None)
    with pytest.raises(ValueError):
        up.validate_assumptions({'treatment_probability': 1.5, 'retention_probability_by_contract': {'Month-to-month': .3}})
    with pytest.raises(ValueError):
        up.validate_assumptions({'treatment_probability': .5, 'retention_probability_by_contract': {'Month-to-month': 2}})


def test_simulated_treatment_follows_documented_rule(test_frame, cfg):
    X, y = test_frame
    s = up.simulate_treatment(X, y, cfg)
    assert abs(s.simulated_treatment.mean() - cfg['treatment_probability']) < 0.05
    # control outcomes are untouched; treated outcomes can only go churn -> retained, never the reverse
    assert (s.loc[s.simulated_treatment == 0, 'simulated_outcome'] == y[s.simulated_treatment == 0]).all()
    assert (s.simulated_outcome <= y).all()
    # randomised: treatment independent of Contract
    shares = s.groupby(X['Contract']).simulated_treatment.mean()
    assert shares.max() - shares.min() < 0.1
    # Two-year contract has retention probability 0 -> outcomes never change there
    two = X['Contract'] == 'Two year'
    assert (s.loc[two, 'simulated_outcome'] == y[two]).all()
    # reproducible
    assert up.simulate_treatment(X, y, cfg).equals(s)


def test_effect_follows_assumption(test_frame, cfg):
    X, y = test_frame
    big = {**cfg, 'treatment_probability': .99, 'retention_probability_by_contract': {'Month-to-month': 1.0, 'One year': 0.0, 'Two year': 0.0}}
    s = up.simulate_treatment(X, y, big)
    m2m = (X['Contract'] == 'Month-to-month') & (s.simulated_treatment == 1)
    assert (s.loc[m2m, 'simulated_outcome'] == 0).all()
    assert (s.loc[X['Contract'] != 'Month-to-month', 'simulated_outcome'] == y[X['Contract'] != 'Month-to-month']).all()


def test_t_learner_two_models_and_everything_labelled_simulated(ub, bundle, test_frame):
    X, _ = test_frame
    assert ub['models']['treated_model'] is not ub['models']['control_model']
    assert ub['models']['n_treated'] + ub['models']['n_control'] == 5634
    out = up.score_uplift(X.iloc[:50], ub, bundle['meta'])
    assert (out['label'] == 'SIMULATED').all() and out['note'].str.contains('SIMULATED').all()
    assert ub['label'] == 'SIMULATED' and 'SIMULATED' in ub['note'] and ub['evaluation']['label'] == 'SIMULATED'
    assert np.isfinite(out['simulated_uplift']).all()
    assert np.allclose(out['simulated_uplift'], out['simulated_p_churn_control'] - out['simulated_p_churn_treated'])


def test_pipeline_recovers_assumed_uplift_ordering(ub):
    """Pipeline check only (not a finding): the learner should rank customers like the simulation assumed."""
    ev = ub['evaluation']
    assert ev['corr_estimated_vs_assumed_uplift_test'] > 0.5
    assert ev['sim_churn_rate_treated_train'] < ev['sim_churn_rate_control_train']


def test_uplift_is_larger_for_month_to_month_as_assumed(ub, bundle, test_frame):
    X, _ = test_frame
    u = up.score_uplift(X, ub, bundle['meta'])['simulated_uplift']
    by = u.groupby(X['Contract']).mean()
    assert by['Month-to-month'] > by['Two year']


def test_artifact_roundtrip(ub, tmp_path, bundle):
    import joblib
    p = tmp_path / 'x.joblib'
    joblib.dump(ub, p)
    assert up.load(p)['label'] == 'SIMULATED'
    assert up.load(tmp_path / 'nope.joblib') is None
