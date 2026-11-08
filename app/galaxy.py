"""Customer Galaxy: the held-out test customers laid out in space, with a per-customer digital twin and whole-galaxy campaigns.

Every number comes from the SERVED calibrated model (or from MonthlyCharges in the data). Nothing here is a causal estimate and no
business number is invented:
  * layout   - angle = where the customer sits among similar customers (PCA of the model's own feature space, spread evenly round the circle); distance from the
               core = churn risk (closer = more likely to leave). Pure presentation of the model's scores.
  * twin     - churn_serving + app.twin.simulate(): model sensitivity, NOT a causal estimate.
  * campaign - the same twin applied to many customers at once; the totals are sums of model probabilities.
  * timeline - ILLUSTRATIVE only: the Telco data has no time axis, so we read the model probability as "leaves within 12 months" with a
               constant monthly hazard. It is a way to look at the scores over time, not a forecast.
The customers are the 20% hold-out split (the model never trained on them), so the real outcome ('churned') can be revealed next to the score.
"""
from __future__ import annotations

import threading
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.model_selection import train_test_split
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

import churn_serving as cs
from app import twin as tw

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / 'WA_Fn-UseC_-Telco-Customer-Churn.csv'
HORIZON_MONTHS = 12
LOOKALIKES = 6
R_IN, R_OUT, TWIST = 0.18, 1.0, 1.1          # layout constants (unit-less scene coordinates)
TIMELINE_NOTE = ('ILLUSTRATIVE: the data has no time axis, so the model probability is read as "leaves within %d months" with a constant '
                 'monthly hazard. It shows the scores over time; it is not a forecast.' % HORIZON_MONTHS)
LAYOUT_NOTE = 'Angle = which customers look alike to the model; distance from the core = churn risk (closer = more likely to leave).'
CAMPAIGN_NOTE = ('Model sensitivity, not a causal estimate: totals are sums of the served model\'s probabilities after editing the '
                 'inputs. Offer and campaign costs are not set in config.yaml, so they are not subtracted.')
_LOCK = threading.Lock()


def load_holdout(csv=CSV) -> tuple[pd.DataFrame, pd.Series]:
    """The 20% test split, rebuilt exactly as train.py does (seed 42, stratified). Index = original CSV row."""
    df = pd.read_csv(csv).drop(columns=['customerID'])
    df['TotalCharges'] = pd.to_numeric(df['TotalCharges'], errors='coerce')
    y = df['Churn'].map({'Yes': 1, 'No': 0})
    _, X_test, _, y_test = train_test_split(df.drop(columns=['Churn']), y, test_size=0.2, random_state=42, stratify=y)
    return X_test, y_test


def _spread_angle(pcs: np.ndarray, ang_sorted: np.ndarray) -> np.ndarray:
    """Similarity angle, re-spaced so the galaxy's customers fill the full circle evenly (rank of the raw PCA angle among all customers).
    Order is kept, so neighbours stay neighbours; an edited customer is ranked against the same fixed galaxy."""
    raw = np.arctan2(pcs[:, 1], pcs[:, 0])
    return 2 * np.pi * np.searchsorted(ang_sorted, raw, side='left') / len(ang_sorted)


def _layout(pcs: np.ndarray, p: np.ndarray, jit: np.ndarray, ang_sorted: np.ndarray) -> np.ndarray:
    """Scene position from the similarity coordinates (pcs) and the risk (p). Risk sets the radius, so a lower risk moves a node outwards."""
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    r = R_IN + (R_OUT - R_IN) * (1.0 - p) ** 0.8 + jit[:, 1]
    th = _spread_angle(pcs, ang_sorted) + jit[:, 0] + TWIST * r
    z = np.clip(pcs[:, 2] * 0.10 * (0.35 + r), -0.35, 0.35) + jit[:, 2]
    return np.column_stack([r * np.cos(th), r * np.sin(th), z])


def _positions(g: dict, frame: pd.DataFrame, p: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """Scene positions for edited customers. idx = the galaxy node each row belongs to (keeps that node's own jitter)."""
    pcs = g['pca'].transform(g['scaler'].transform(_encode(g, frame))) / g['pc_std']
    return _layout(pcs, p, g['jit'][idx], g['ang_sorted'])


def _encode(g: dict, frame: pd.DataFrame) -> np.ndarray:
    Z = g['prep'].transform(cs.prepare(frame, g['meta']))
    return Z.toarray() if hasattr(Z, 'toarray') else np.asarray(Z)


def _r(a, nd=4):
    return np.round(np.asarray(a, dtype=float), nd).tolist()


def build(bundle: dict, seed: int = 42) -> dict:
    """Build (once per bundle) and return the galaxy state. Thread-safe; the first call scores ~1400 customers (about a second)."""
    with _LOCK:
        if '_galaxy' in bundle:
            return bundle['_galaxy']
        meta = bundle['meta']
        X, y = load_holdout()
        frame = X[meta['input_columns']].reset_index(drop=True)
        orig_index = X.index.to_numpy()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            cs.prepare(frame, meta)                                   # validate every row once, up front
            pred = cs.predict(frame, bundle)
        p = pred['churn_probability'].to_numpy()
        prep = bundle['explainer_pipeline'].named_steps['prep']       # the model's own feature encoder (similarity space)
        g = {'meta': meta, 'prep': prep}
        Z = _encode(g, frame)
        g['scaler'] = StandardScaler().fit(Z)
        Zs = g['scaler'].transform(Z)
        g['pca'] = PCA(n_components=3, random_state=seed).fit(Zs)
        g['pc_std'] = np.sqrt(g['pca'].explained_variance_)
        pcs = g['pca'].transform(Zs) / g['pc_std']
        rng = np.random.default_rng(seed)
        g['jit'] = np.column_stack([rng.normal(0, 0.03, len(frame)), rng.normal(0, 0.012, len(frame)), rng.normal(0, 0.02, len(frame))])
        g['ang_sorted'] = np.sort(np.arctan2(pcs[:, 1], pcs[:, 0]))
        pos = _layout(pcs, p, g['jit'], g['ang_sorted'])
        u = rng.random(len(frame))                                    # one fixed draw per customer, shared by every "world"
        nn = NearestNeighbors(n_neighbors=LOOKALIKES + 1).fit(Zs)
        nbrs = nn.kneighbors(Zs, return_distance=False)[:, 1:]
        g['twins'] = [tw.CustomerTwin(r, meta, _validated=True) for r in frame.to_dict('records')]
        g['frame'], g['p'], g['y'] = frame, p, y.to_numpy()
        threshold, source, placeholder = cs.get_threshold(meta, bundle.get('config'))
        g['threshold'] = threshold
        nodes = []
        for i, row in enumerate(frame.to_dict('records')):
            nodes.append({'id': i, 'ref': 'C-%04d' % orig_index[i], 'p': round(float(p[i]), 4), 'pos': _r(pos[i]), 'u': round(float(u[i]), 4),
                          'churned': int(g['y'][i]), 'nb': nbrs[i].tolist(), 'c': row})
        mc = frame['MonthlyCharges'].to_numpy()
        g['payload'] = {
            'nodes': nodes,
            'summary': {'n': len(nodes), 'flagged': int((p >= threshold).sum()), 'mean_risk': float(p.mean()),
                        'expected_churners': float(p.sum()), 'actual_churners': int(g['y'].sum()),
                        'monthly_revenue': float(mc.sum()), 'monthly_revenue_at_risk': float((p * mc).sum())},
            'threshold': threshold, 'threshold_source': source, 'threshold_is_placeholder': placeholder,
            'horizon_months': HORIZON_MONTHS, 'timeline_note': TIMELINE_NOTE, 'layout_note': LAYOUT_NOTE,
            'campaign_note': CAMPAIGN_NOTE, 'twin_label': tw.INTERPRETATION,
            'data_note': 'The %d customers are the hold-out test split: the model never trained on them, so the real outcome can be shown next to the score.' % len(nodes),
            'test_auc': (meta.get('test_metrics') or {}).get('roc_auc'),
        }
        bundle['_galaxy'] = g
        return g


# ------------------------------------------------------------------ one customer
def _node(g: dict, node_id: int) -> int:
    if isinstance(node_id, bool) or not isinstance(node_id, int) or not 0 <= node_id < len(g['twins']):
        raise tw.TwinError('unknown galaxy customer id %r' % (node_id,))
    return node_id


def twin_node(bundle: dict, node_id: int, changes: dict) -> dict:
    """What-if for one galaxy customer: the twin result, where the node would move to, and the SHAP drivers of the edited customer."""
    g = build(bundle)
    i = _node(g, node_id)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        res = tw.simulate(g['twins'][i], bundle, changes)
        after = pd.DataFrame([res['resulting_customer']])
        pos = _positions(g, after, np.array([res['risk_after']]), np.array([i]))[0]
        contrib, _, _ = cs.explain(after, bundle)
    c = contrib.iloc[0]
    drivers = [{'feature': k, 'value': res['resulting_customer'][k], 'push': round(float(c[k]), 4)}
               for k in c.abs().sort_values(ascending=False).index[:7]]
    return {'id': i, 'interpretation': tw.INTERPRETATION, 'baseline_risk': res['baseline_risk'], 'risk_after': res['risk_after'],
            'risk_drop': res['risk_drop'], 'flagged_before': res['flagged_before'], 'flagged_after': res['flagged_after'],
            'pos_after': _r(pos), 'monthly_after': res['resulting_customer']['MonthlyCharges'], 'drivers': drivers,
            'confidence': res['confidence'], 'confidence_reason': res['confidence_reason'], 'warnings': res['warnings'],
            'resulting_customer': res['resulting_customer'], 'drivers_note': 'SHAP log-odds pushes of the edited customer; they describe the model, not a cause'}


# ------------------------------------------------------------------ whole galaxy
def _check_changes(changes: dict, meta: dict) -> None:
    """Reject a bad edit once, up front, so it is not mistaken for 'not applicable to anyone'."""
    if not changes:
        raise tw.TwinError('a campaign needs at least one change')
    for k, v in changes.items():
        if k == 'discount_pct':
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= tw.MAX_DISCOUNT_PCT:
                raise tw.TwinError('discount_pct must be between 0 and %d, got %r' % (tw.MAX_DISCOUNT_PCT, v))
        elif k in tw.FORBIDDEN_REASON:
            raise tw.TwinError('Cannot change %s: %s' % (k, tw.FORBIDDEN_REASON[k]))
        elif k not in tw.EDITABLE:
            raise tw.TwinError('Unknown field %r. Allowed: %s and discount_pct' % (k, tw.EDITABLE))
        elif v not in meta['categorical_levels'][k]:
            raise tw.TwinError('%s=%r is not an allowed value; allowed: %s' % (k, v, meta['categorical_levels'][k]))


def campaign(bundle: dict, *, scenario: str | None = None, changes: dict | None = None, flagged_only: bool = False,
             contracts: list[str] | None = None) -> dict:
    """Apply one edit to every targeted, applicable customer and re-score the galaxy in one batch."""
    g = build(bundle)
    meta = bundle['meta']
    sc = None
    if scenario is not None:
        if scenario not in bundle['scenarios']:
            raise tw.TwinError('Unknown scenario %r; available: %s' % (scenario, list(bundle['scenarios'])))
        sc = bundle['scenarios'][scenario]
        changes = sc['changes']
    changes = dict(changes or {})
    _check_changes(changes, meta)
    p0, frame = g['p'], g['frame']
    mc0 = frame['MonthlyCharges'].to_numpy()
    n = len(p0)
    target = np.ones(n, dtype=bool)
    if flagged_only:
        target &= p0 >= g['threshold']
    if contracts:
        target &= frame['Contract'].isin(contracts).to_numpy()
    touched, rows, skipped = [], [], Counter()
    for i in np.flatnonzero(target):
        twin = g['twins'][i]
        reason = tw._not_applicable(sc, twin) if sc else None
        if reason is None:
            try:
                rows.append(twin.apply(changes))
            except tw.TwinError as e:
                reason = str(e)
        if reason is not None:
            skipped[reason.split(';')[0][:90]] += 1
            continue
        touched.append(int(i))
    p1, mc1 = p0.copy(), mc0.copy()
    pos_after = None
    if touched:
        idx = np.array(touched)
        after = pd.DataFrame([t.as_dict() for t in rows])
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            pa = cs.predict(after, bundle)['churn_probability'].to_numpy()
            pos_t = _positions(g, after, pa, idx)
        p1[idx] = pa
        mc1[idx] = after['MonthlyCharges'].to_numpy()
        pos_after = {int(i): _r(pos_t[k], 4) for k, i in enumerate(idx)}
    thr = g['threshold']
    sm = lambda p, mc: {'expected_churners': float(p.sum()), 'flagged': int((p >= thr).sum()),
                        'monthly_revenue_kept': float(((1 - p) * mc).sum()), 'monthly_revenue_at_risk': float((p * mc).sum())}
    return {'interpretation': tw.INTERPRETATION, 'note': CAMPAIGN_NOTE, 'changes': changes, 'scenario': scenario,
            'warnings': [tw.CONTRACT_WARNING] if 'Contract' in changes else [],
            'targeted': int(target.sum()), 'affected': len(touched), 'skipped': sum(skipped.values()),
            'skipped_reasons': [{'reason': k, 'count': v} for k, v in skipped.most_common(3)],
            'before': sm(p0, mc0), 'after': sm(p1, mc1),
            'touched': touched, 'p_after': _r(p1[touched]) if touched else [], 'mc_after': _r(mc1[touched], 2) if touched else [],
            'pos_after': [pos_after[i] for i in touched] if touched else [], 'threshold': thr}
