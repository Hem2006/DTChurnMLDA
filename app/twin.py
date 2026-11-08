"""Digital twin / what-if simulator.

This is a MODEL-SENSITIVITY tool, NOT a causal estimate: it shows how the served model's score moves when inputs are edited.
It does not say what would happen if the business actually made the change.
"""
from __future__ import annotations

import warnings
from types import MappingProxyType
from typing import Any, Mapping

import numpy as np
import pandas as pd
import yaml

import churn_serving as cs

INTERPRETATION = 'model sensitivity, not a causal estimate'
CONTRACT_WARNING = ("Contract change: most of the model's effect is selection (loyal customers choose long contracts), "
                    'so the real-world drop is likely much smaller than shown.')
MAX_DISCOUNT_PCT = 30
AGREEMENT_MAX_GAP = 0.10

ADDONS = ['OnlineSecurity', 'OnlineBackup', 'DeviceProtection', 'TechSupport', 'StreamingTV', 'StreamingMovies']
NO_INTERNET, NO_PHONE = 'No internet service', 'No phone service'
# Fields a what-if may touch. Demographics (SeniorCitizen, Partner, Dependents) are not levers the business can pull.
EDITABLE = ['PhoneService', 'MultipleLines', 'InternetService'] + ADDONS + ['Contract', 'PaperlessBilling', 'PaymentMethod']
FORBIDDEN_REASON = {
    'tenure': 'tenure never changes (a Contract change does not alter tenure)',
    'MonthlyCharges': "MonthlyCharges cannot be edited directly; use 'discount_pct' (0-%d)" % MAX_DISCOUNT_PCT,
    'TotalCharges': 'TotalCharges is not a model input',
    'gender': 'gender is not a model input',
    'SeniorCitizen': 'SeniorCitizen is not an actionable lever',
    'Partner': 'Partner is not an actionable lever',
    'Dependents': 'Dependents is not an actionable lever',
}


class TwinError(ValueError):
    """A requested change is unknown, not allowed, or would make the customer incoherent."""


def _check_coherent(row: Mapping[str, Any], levels: Mapping[str, list], where: str) -> None:
    problems = []
    for c, allowed in levels.items():
        if row[c] not in allowed:
            problems.append('%s=%r is not an allowed value %s' % (c, row[c], allowed))
    if row['InternetService'] == 'No':
        bad = [c for c in ADDONS if row[c] != NO_INTERNET]
        if bad:
            problems.append("InternetService=No requires %s = '%s' (none may be Yes/No)" % (bad, NO_INTERNET))
    else:
        bad = [c for c in ADDONS if row[c] == NO_INTERNET]
        if bad:
            problems.append("%s can only be '%s' when InternetService=No" % (bad, NO_INTERNET))
    if row['PhoneService'] == 'No' and row['MultipleLines'] != NO_PHONE:
        problems.append("PhoneService=No requires MultipleLines='%s'" % NO_PHONE)
    if row['PhoneService'] == 'Yes' and row['MultipleLines'] == NO_PHONE:
        problems.append("MultipleLines='%s' only when PhoneService=No" % NO_PHONE)
    if problems:
        raise TwinError('%s is incoherent: %s' % (where, '; '.join(problems)))


class CustomerTwin:
    """One customer as the model sees them. The original snapshot is immutable; apply() returns a new twin."""
    __slots__ = ('_row', '_base_monthly', '_discount_pct', '_meta')

    def __init__(self, customer: Mapping[str, Any], meta: Mapping[str, Any], *, _base_monthly=None, _discount_pct=0.0, _validated=False):
        cols = meta['input_columns']
        missing = [c for c in cols if c not in customer]
        extra = [c for c in customer if c not in cols]
        if missing or extra:
            raise TwinError('customer must have exactly the %d model input fields; missing %s, unexpected %s' % (len(cols), missing, extra))
        row = {c: customer[c] for c in cols}
        _check_coherent(row, meta['categorical_levels'], 'customer')
        # Reuse the serving validator so a twin can never hold a row the model would reject. _validated=True is for callers that
        # already ran cs.prepare on these exact values (apply(), or a whole frame validated in one go); it only skips the slow repeat.
        if not _validated:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                cs.prepare(pd.DataFrame([row]), meta)
        object.__setattr__(self, '_row', MappingProxyType(dict(row)))
        object.__setattr__(self, '_base_monthly', float(row['MonthlyCharges'] if _base_monthly is None else _base_monthly))
        object.__setattr__(self, '_discount_pct', float(_discount_pct))
        object.__setattr__(self, '_meta', meta)

    def __setattr__(self, k, v):
        raise AttributeError('CustomerTwin is immutable; use apply() to get a new twin')

    @property
    def row(self) -> Mapping[str, Any]:
        return self._row

    @property
    def discount_pct(self) -> float:
        return self._discount_pct

    def as_dict(self) -> dict:
        return dict(self._row)

    def apply(self, changes: Mapping[str, Any] | None) -> 'CustomerTwin':
        """Return a NEW twin with the changes applied and coherence enforced. Raises TwinError otherwise. Never mutates self."""
        changes = dict(changes or {})
        levels = self._meta['categorical_levels']
        row = dict(self._row)
        discount = self._discount_pct
        for k, v in changes.items():
            if k == 'discount_pct':
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not 0 <= v <= MAX_DISCOUNT_PCT:
                    raise TwinError('discount_pct must be a number between 0 and %d, got %r' % (MAX_DISCOUNT_PCT, v))
                discount = float(v)
            elif k in FORBIDDEN_REASON:
                raise TwinError('Cannot change %s: %s' % (k, FORBIDDEN_REASON[k]))
            elif k not in EDITABLE:
                raise TwinError('Unknown field %r. Allowed: %s and discount_pct' % (k, EDITABLE))
            else:
                if v not in levels[k]:
                    raise TwinError('%s=%r is not an allowed value; allowed: %s' % (k, v, levels[k]))
                row[k] = v
        # Coherence: forced values may only be set implicitly; an explicit contradictory value is an error.
        if row['InternetService'] == 'No':
            for c in ADDONS:
                if c in changes and changes[c] != NO_INTERNET:
                    raise TwinError("InternetService=No forces %s='%s'; cannot set it to %r" % (c, NO_INTERNET, changes[c]))
                row[c] = NO_INTERNET
        if row['PhoneService'] == 'No':
            if 'MultipleLines' in changes and changes['MultipleLines'] != NO_PHONE:
                raise TwinError("PhoneService=No forces MultipleLines='%s'; cannot set it to %r" % (NO_PHONE, changes['MultipleLines']))
            row['MultipleLines'] = NO_PHONE
        _check_coherent(row, levels, 'result')
        row['tenure'] = self._row['tenure']                                   # never changes, whatever the Contract
        row['MonthlyCharges'] = round(self._base_monthly * (1 - discount / 100.0), 10)
        # Every categorical value was checked against the allowed levels above and the numerics derive from a validated twin.
        return CustomerTwin(row, self._meta, _base_monthly=self._base_monthly, _discount_pct=discount, _validated=True)

    def score(self, bundle) -> dict:
        """Probability and flag from the SERVED calibrated model."""
        out = cs.predict(pd.DataFrame([dict(self._row)]), bundle).iloc[0]
        return {'probability': float(out['churn_probability']), 'flagged': bool(out['flagged'])}

    def score_reference(self, bundle) -> float | None:
        """Probability from the plain logistic-regression reference model (agreement check only)."""
        ref = bundle.get('logreg_ref')
        if ref is None:
            return None
        X = cs.prepare(pd.DataFrame([dict(self._row)]), bundle['meta'])
        return float(ref.predict_proba(X)[0, 1])


# ------------------------------------------------------------------ scenarios
def load_scenarios(path='scenarios.yaml') -> dict:
    raw = yaml.safe_load(open(path, encoding='utf8').read())['scenarios']
    out = {}
    for s in raw:
        if s['name'] in out:
            raise ValueError('duplicate scenario %r' % s['name'])
        out[s['name']] = {'name': s['name'], 'description': s.get('description', ''), 'changes': dict(s.get('changes') or {}),
                          'cost_per_customer': s.get('cost_per_customer'), 'requires': s.get('requires') or {},
                          'not_applicable_if': s.get('not_applicable_if') or {}}
    return out


def _not_applicable(sc: Mapping, twin: CustomerTwin) -> str | None:
    row = twin.row
    for field, allowed in sc['requires'].items():
        if row[field] not in allowed:
            return 'requires %s in %s; customer has %s' % (field, allowed, row[field])
    for field, vals in sc['not_applicable_if'].items():
        if row[field] in vals:
            return 'not applicable because %s is already %s' % (field, row[field])
    return None


def _cost(name: str, scenario_cost, config: Mapping) -> float | None:
    override = (config.get('scenario_costs') or {}).get(name)
    return override if override is not None else scenario_cost


def simulate(customer, bundle, changes: Mapping[str, Any], *, name: str = 'custom', cost_per_customer=None) -> dict:
    """THE single implementation of a what-if. run_scenarios() and the /simulate endpoint both call it.
    Raises TwinError for any incoherent or disallowed change."""
    meta, config = bundle['meta'], bundle.get('config') or {}
    base = customer if isinstance(customer, CustomerTwin) else CustomerTwin(customer, meta)
    after = base.apply(changes)
    b, a = base.score(bundle), after.score(bundle)
    drop = b['probability'] - a['probability']
    warns = []
    if after.row['Contract'] != base.row['Contract']:
        warns.append(CONTRACT_WARNING)
    realization = config.get('twin_effect_realization')
    adjusted = drop * realization if realization is not None else None

    # Expected net value: only from numbers in config.yaml / scenarios.yaml; never invented.
    sr, cv = config.get('success_rate'), config.get('customer_value')
    cost = _cost(name, cost_per_customer, config)
    missing = [k for k, v in (('success_rate', sr), ('customer_value', cv), ('cost_per_customer', cost)) if v is None]
    if missing:
        ev, ev_reason, basis = None, 'not computed: %s not set in config.yaml/scenarios.yaml' % ', '.join(missing), None
    else:
        basis_drop = adjusted if adjusted is not None else drop
        ev, ev_reason, basis = basis_drop * sr * cv - cost, None, ('adjusted_risk_drop' if adjusted is not None else 'raw_risk_drop')

    # Agreement check against the plain logistic-regression reference.
    lr_b, lr_a = base.score_reference(bundle), after.score_reference(bundle)
    if lr_b is None:
        confidence, why, lr = 'unknown', 'logistic-regression reference model not loaded', None
    else:
        lr_drop = lr_b - lr_a
        lr = {'baseline_risk': lr_b, 'risk_after': lr_a, 'risk_drop': lr_drop}
        reasons = []
        if np.sign(drop) != np.sign(lr_drop):
            reasons.append('the two models disagree on direction (served %+.3f vs logistic regression %+.3f)' % (-drop, -lr_drop))
        if abs(drop - lr_drop) > AGREEMENT_MAX_GAP:
            reasons.append('risk_drop differs by %.3f (> %.2f) between the served model (%.3f) and logistic regression (%.3f)'
                           % (abs(drop - lr_drop), AGREEMENT_MAX_GAP, drop, lr_drop))
        confidence, why = ('low', '; '.join(reasons)) if reasons else ('ok', 'served model and logistic-regression reference agree in sign and within %.2f' % AGREEMENT_MAX_GAP)

    return {'name': name, 'applicable': True, 'interpretation': INTERPRETATION, 'changes': dict(changes),
            'baseline_risk': b['probability'], 'risk_after': a['probability'], 'risk_drop': drop,
            'flagged_before': b['flagged'], 'flagged_after': a['flagged'],
            'twin_effect_realization': realization, 'risk_drop_adjusted': adjusted,
            'expected_net_value': ev, 'expected_net_value_reason': ev_reason, 'expected_net_value_basis': basis,
            'cost_per_customer': cost, 'warnings': warns,
            'confidence': confidence, 'confidence_reason': why, 'reference_logreg': lr,
            'resulting_customer': after.as_dict()}


def run_scenarios(customer, bundle, scenario_names=None, scenarios: Mapping | None = None) -> dict:
    """Run library scenarios for one customer. Inapplicable scenarios come back as not_applicable with a reason, never skipped."""
    scenarios = scenarios if scenarios is not None else bundle['scenarios']
    names = list(scenarios) if scenario_names is None else list(scenario_names)
    unknown = [n for n in names if n not in scenarios]
    if unknown:
        raise TwinError('Unknown scenario(s) %s; available: %s' % (unknown, list(scenarios)))
    meta = bundle['meta']
    twin = customer if isinstance(customer, CustomerTwin) else CustomerTwin(customer, meta)
    results, na = [], []
    for n in names:
        sc = scenarios[n]
        reason = _not_applicable(sc, twin)
        if reason is None:
            try:
                twin.apply(sc['changes'])
            except TwinError as e:                                              # coherence failure for this customer
                reason = str(e)
        if reason is not None:
            na.append({'name': n, 'applicable': False, 'reason': reason, 'interpretation': INTERPRETATION})
            continue
        results.append(simulate(twin, bundle, sc['changes'], name=n, cost_per_customer=sc['cost_per_customer']))
    if results and all(r['expected_net_value'] is not None for r in results):
        results.sort(key=lambda r: r['expected_net_value'], reverse=True); ranked_by = 'expected_net_value'
    else:
        results.sort(key=lambda r: r['risk_drop'], reverse=True); ranked_by = 'risk_drop'
    base = twin.score(bundle)
    return {'interpretation': INTERPRETATION, 'ranked_by': ranked_by, 'baseline_risk': base['probability'], 'flagged_before': base['flagged'],
            'results': results, 'not_applicable': na}
