"""Digital twin / what-if simulator.

MODEL SENSITIVITY, NOT A CAUSAL ESTIMATE: it shows how the served model's score moves when inputs are edited. It does not say what the
business would achieve by making the change.

A twin is an immutable copy of one customer. Editable: Contract, the six add-on services, PaymentMethod, PaperlessBilling and a discount of
0-30%. Blocked: tenure, demographics, gender, TotalCharges and MonthlyCharges (only changed through the discount).
Delta = p(x) - p(x'): positive means the model scores the edited customer as LESS likely to churn.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src import serving as S
from src.data import ADDONS, INPUTS, NO_INTERNET

INTERPRETATION = 'model sensitivity, not a causal estimate'
CONTRACT_WARNING = ("Contract change: most of the model's effect is selection (loyal customers choose long contracts), "
                    'so the real-world effect is likely much smaller than shown.')
MAX_DISCOUNT_PCT = 30
AGREEMENT_MAX_GAP = 0.10
EDITABLE = ['Contract', 'PaymentMethod', 'PaperlessBilling'] + ADDONS
BLOCKED = {
    'tenure': 'tenure never changes (a Contract change does not alter tenure)',
    'MonthlyCharges': "MonthlyCharges cannot be edited directly; use 'discount_pct' (0-%d)" % MAX_DISCOUNT_PCT,
    'TotalCharges': 'TotalCharges is not a model input',
    'gender': 'gender is not a model input',
    'SeniorCitizen': 'demographics are not levers the business can pull',
    'Partner': 'demographics are not levers the business can pull',
    'Dependents': 'demographics are not levers the business can pull',
    'InternetService': 'changing the type of internet service is not a retention lever',
    'PhoneService': 'changing phone service is not a retention lever',
    'MultipleLines': 'changing phone lines is not a retention lever',
}


class TwinError(ValueError):
    """A change is unknown, blocked, or would make the customer incoherent."""


class CustomerTwin:
    __slots__ = ('_row', '_base_monthly', '_discount', '_meta')

    def __init__(self, customer: Mapping[str, Any], meta: Mapping, *, _base_monthly=None, _discount=0.0):
        missing = [c for c in INPUTS if c not in customer]
        extra = [c for c in customer if c not in INPUTS]
        if missing or extra:
            raise TwinError('customer must have exactly the 17 model inputs; missing %s, unexpected %s' % (missing, extra))
        row = {c: customer[c] for c in INPUTS}
        try:
            S.check_coherent(row, meta['levels'])
            S.prepare(pd.DataFrame([row]), meta)
        except S.InputError as e:
            raise TwinError('customer is invalid: %s' % e) from None
        object.__setattr__(self, '_row', MappingProxyType(row))
        object.__setattr__(self, '_base_monthly', float(row['MonthlyCharges'] if _base_monthly is None else _base_monthly))
        object.__setattr__(self, '_discount', float(_discount))
        object.__setattr__(self, '_meta', meta)

    def __setattr__(self, k, v):
        raise AttributeError('CustomerTwin is immutable; use apply() to get a new twin')

    @property
    def row(self):
        return self._row

    @property
    def discount_pct(self):
        return self._discount

    def as_dict(self) -> dict:
        return dict(self._row)

    def apply(self, changes: Mapping[str, Any] | None) -> 'CustomerTwin':
        """A NEW twin with the changes applied. Never mutates self."""
        changes = dict(changes or {})
        levels = self._meta['levels']
        row, discount = dict(self._row), self._discount
        for k, v in changes.items():
            if k == 'discount_pct':
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not 0 <= v <= MAX_DISCOUNT_PCT:
                    raise TwinError('discount_pct must be between 0 and %d, got %r' % (MAX_DISCOUNT_PCT, v))
                discount = float(v)
            elif k in BLOCKED:
                raise TwinError('Cannot change %s: %s' % (k, BLOCKED[k]))
            elif k not in EDITABLE:
                raise TwinError('Unknown field %r. Editable: %s and discount_pct' % (k, EDITABLE))
            elif v not in levels[k]:
                raise TwinError('%s=%r is not allowed; allowed: %s' % (k, v, levels[k]))
            else:
                row[k] = v
        if row['InternetService'] == 'No':
            bad = [c for c in changes if c in ADDONS and changes[c] != NO_INTERNET]
            if bad:
                raise TwinError('Cannot set %s: the customer has no internet service' % bad)
            for c in ADDONS:
                row[c] = NO_INTERNET
        elif any(changes.get(c) == NO_INTERNET for c in ADDONS):
            raise TwinError("'%s' is only valid for customers without internet" % NO_INTERNET)
        row['tenure'] = self._row['tenure']
        row['MonthlyCharges'] = round(self._base_monthly * (1 - discount / 100.0), 10)
        return CustomerTwin(row, self._meta, _base_monthly=self._base_monthly, _discount=discount)


def _score(twin: CustomerTwin, bundle: dict) -> tuple[float, float]:
    df = pd.DataFrame([dict(twin.row)])
    return float(S.predict(df, bundle)['churn_probability'].iloc[0]), float(S.reference_probability(df, bundle).iloc[0])


def simulate(customer, bundle: dict, changes: Mapping[str, Any], *, name: str = 'custom', cost_per_customer=None) -> dict:
    """THE single implementation of a what-if. Raises TwinError for disallowed or incoherent changes."""
    meta = bundle['meta']
    base = customer if isinstance(customer, CustomerTwin) else CustomerTwin(customer, meta)
    after = base.apply(changes)
    (pb, lb), (pa, la) = _score(base, bundle), _score(after, bundle)
    delta, ref_delta = pb - pa, lb - la
    reasons = []
    if np.sign(delta) != np.sign(ref_delta) and not (abs(delta) < 1e-9 and abs(ref_delta) < 1e-9):
        reasons.append('the served model and the logistic reference disagree on direction (%+.3f vs %+.3f)' % (delta, ref_delta))
    if abs(delta - ref_delta) > AGREEMENT_MAX_GAP:
        reasons.append('the two models differ by %.3f (> %.2f) on the size of the change' % (abs(delta - ref_delta), AGREEMENT_MAX_GAP))
    warns = [CONTRACT_WARNING] if after.row['Contract'] != base.row['Contract'] else []
    t = bundle['threshold']
    return {'name': name, 'interpretation': INTERPRETATION, 'changes': dict(changes), 'baseline_risk': pb, 'risk_after': pa, 'delta': delta,
            'flagged_before': pb >= t['threshold'], 'flagged_after': pa >= t['threshold'],
            'reference': {'baseline_risk': lb, 'risk_after': la, 'delta': ref_delta},
            'confidence': 'low' if reasons else 'ok',
            'confidence_reason': '; '.join(reasons) if reasons else 'served model and logistic reference agree in sign and within %.2f' % AGREEMENT_MAX_GAP,
            'warnings': warns, 'cost_per_customer': cost_per_customer, 'resulting_customer': after.as_dict()}


def _not_applicable(sc: Mapping, twin: CustomerTwin) -> str | None:
    for f, allowed in (sc.get('requires') or {}).items():
        if twin.row[f] not in allowed:
            return 'requires %s in %s; customer has %s' % (f, allowed, twin.row[f])
    for f, vals in (sc.get('not_applicable_if') or {}).items():
        if twin.row[f] in vals:
            return '%s is already %s' % (f, twin.row[f])
    return None


def run_scenarios(customer, bundle: dict, names=None) -> dict:
    """Library scenarios for one customer, ranked by model-sensitivity delta. Inapplicable ones are reported with a reason."""
    meta = bundle['meta']
    twin = customer if isinstance(customer, CustomerTwin) else CustomerTwin(customer, meta)
    lib = {s['name']: s for s in bundle['scenarios']}
    names = list(lib) if names is None else list(names)
    unknown = [n for n in names if n not in lib]
    if unknown:
        raise TwinError('Unknown scenario(s) %s' % unknown)
    results, na = [], []
    for n in names:
        sc = lib[n]
        reason = _not_applicable(sc, twin)
        if reason is None:
            try:
                twin.apply(sc.get('changes') or {})
            except TwinError as e:
                reason = str(e)
        if reason:
            na.append({'name': n, 'reason': reason})
        else:
            r = simulate(twin, bundle, sc.get('changes') or {}, name=n, cost_per_customer=sc.get('cost_per_customer'))
            r['description'] = sc.get('description', '')
            results.append(r)
    results.sort(key=lambda r: r['delta'], reverse=True)
    return {'interpretation': INTERPRETATION, 'baseline_risk': _score(twin, bundle)[0], 'results': results, 'not_applicable': na}
