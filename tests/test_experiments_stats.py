import numpy as np
import pytest
from sklearn.metrics import roc_auc_score

from experiments.stats import delong_test, ece_quantile, ece_uniform, top_fraction_lift


def _data(seed=0, n=600):
    rng = np.random.default_rng(seed)
    y = rng.integers(0, 2, n)
    p1 = np.clip(0.35 * y + rng.normal(0.3, 0.2, n), 0, 1)
    p2 = np.clip(0.20 * y + rng.normal(0.3, 0.2, n), 0, 1)
    return y, p1, p2


def test_delong_aucs_match_sklearn_and_detect_difference():
    y, p1, p2 = _data()
    r = delong_test(y, p1, p2)
    assert r['auc1'] == pytest.approx(roc_auc_score(y, p1))
    assert r['auc2'] == pytest.approx(roc_auc_score(y, p2))
    assert r['diff'] > 0 and r['p_value'] < 0.001
    assert r['ci95_diff'][0] < r['diff'] < r['ci95_diff'][1]


def test_delong_identical_scores_give_no_difference():
    y, p1, _ = _data(1)
    r = delong_test(y, p1, p1)
    assert r['diff'] == 0 and r['p_value'] == 1.0


def test_delong_se_close_to_bootstrap():
    y, p1, p2 = _data(2)
    rng = np.random.default_rng(3)
    d = []
    for _ in range(400):
        i = rng.integers(0, len(y), len(y))
        d.append(roc_auc_score(y[i], p1[i]) - roc_auc_score(y[i], p2[i]))
    assert delong_test(y, p1, p2)['se'] == pytest.approx(np.std(d), rel=0.2)


def test_delong_needs_both_classes():
    with pytest.raises(ValueError):
        delong_test([1, 1, 1, 0], [.1, .2, .3, .4], [.1, .2, .3, .4])


def test_lift_perfect_and_random():
    y = np.array([1] * 10 + [0] * 90)
    assert top_fraction_lift(y, y.astype(float)) == pytest.approx(10.0)
    assert top_fraction_lift(y, np.zeros(100)) == pytest.approx(10.0)       # all tied: stable sort keeps row order, top 10 are the 1s
    assert top_fraction_lift(y[::-1], np.zeros(100)) == pytest.approx(0.0)


def test_ece_zero_when_calibrated_by_construction():
    p = np.repeat([0.1, 0.5, 0.9], 100)
    y = np.concatenate([np.r_[np.ones(10), np.zeros(90)], np.r_[np.ones(50), np.zeros(50)], np.r_[np.ones(90), np.zeros(10)]])
    assert ece_uniform(y, p) == pytest.approx(0.0)
    assert ece_quantile(y, p, bins=3) == pytest.approx(0.0)
    assert ece_uniform(y, np.full(300, 0.9)) == pytest.approx(0.4)
