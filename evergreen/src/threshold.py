"""Decision threshold. Business numbers come from config/business.yaml; with any of them unset the labelled F1 placeholder is used.

t* = c / (s * v)   (offer cost / (success rate * customer value)): flag a customer when p * s * v > c.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import yaml

KEYS = ('offer_cost', 'customer_value', 'success_rate')
BUSINESS_SOURCE = 'cost_based: offer_cost / (success_rate * customer_value)'
PLACEHOLDER_SOURCE = 'placeholder_f1_optimal_on_calibrated_oof'
PLACEHOLDER_NOTE = ('PLACEHOLDER. No offer cost, customer value or success rate is configured. F1 weighs a missed churner and a wasted '
                    'offer equally, which is not a business claim. Set the three numbers in config/business.yaml.')


def cost_threshold(offer_cost: float, customer_value: float, success_rate: float) -> float:
    """t* = c / (s v). Values >= 1 are legal and mean 'never worth flagging'; invalid inputs raise ValueError."""
    for k, x in zip(KEYS, (offer_cost, customer_value, success_rate)):
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x) or x <= 0:
            raise ValueError('%s must be a positive number, got %r' % (k, x))
    if success_rate > 1:
        raise ValueError('success_rate must be a share in (0, 1]')
    return float(offer_cost / (success_rate * customer_value))


def load_business(path: str | Path) -> dict:
    p = Path(path)
    raw = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
    unknown = set(raw) - set(KEYS)
    if unknown:
        raise ValueError('Unknown keys in %s: %s' % (p, sorted(unknown)))
    return {k: raw.get(k) for k in KEYS}


def resolve(business: dict | None, placeholder: float) -> dict:
    """The threshold in force and where it came from. Always carries `source` and `is_placeholder`."""
    vals = {k: (business or {}).get(k) for k in KEYS}
    if all(v is not None for v in vals.values()):
        return {'threshold': cost_threshold(**vals), 'source': BUSINESS_SOURCE, 'is_placeholder': False, 'note': None, 'inputs': vals}
    return {'threshold': float(placeholder), 'source': PLACEHOLDER_SOURCE, 'is_placeholder': True, 'note': PLACEHOLDER_NOTE, 'inputs': vals}


def f1_optimal(y, p, grid=None) -> float:
    from sklearn.metrics import f1_score
    grid = np.arange(0.02, 0.951, 0.005) if grid is None else grid
    y, p = np.asarray(y), np.asarray(p)
    return float(grid[int(np.argmax([f1_score(y, p >= t, zero_division=0) for t in grid]))])


def profit_curve(y, p, offer_cost: float, customer_value: float, success_rate: float, grid=None):
    """Illustrative profit if every flagged true churner is saved with probability `success_rate` and each flagged customer costs `offer_cost`.
    profit(t) = sum over flagged of (success_rate * customer_value * y - offer_cost). Numbers are whatever the caller passes in."""
    grid = np.linspace(0, 1, 101) if grid is None else grid
    y, p = np.asarray(y, float), np.asarray(p, float)
    return [{'threshold': float(t), 'profit': float((success_rate * customer_value * y[p >= t] - offer_cost).sum()), 'flagged': int((p >= t).sum())}
            for t in grid]
