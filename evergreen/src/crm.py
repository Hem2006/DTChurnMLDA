"""Plain-language layer for CRM users: risk bands, decision wording, setup notices and the batch run. No model jargon leaves this module."""
from __future__ import annotations

import pandas as pd

from src import data as D
from src import db
from src import explain as E
from src import messages as MSG
from src import recommender as R
from src import serving as S
from src import twin as TW

BAND_LABEL = {'high': 'High risk of leaving', 'medium': 'Some risk of leaving', 'low': 'Low risk of leaving'}
DECISION_PLAIN = {'recommend': 'Offer recommended', 'recommend_pending_approval': 'Offer recommended, needs a manager to approve',
                  'no_action': 'No action needed', 'escalate_to_human': 'Hand to a specialist: do not contact'}
RULE_PLAIN = {'vulnerable': 'The account is marked as a vulnerable customer.', 'open_escalation': 'The customer has an open complaint or dispute.',
              'unknown_vulnerable': "We don't know whether the customer is vulnerable.", 'unknown_escalated': "We don't know whether the customer has an open complaint.",
              'contact_cap_unset': 'No monthly contact limit has been set by your admin.', 'contacts_unknown': "We don't know how many times the customer was contacted this month.",
              'contact_cap': 'The customer has already reached the monthly contact limit.', 'discount_cap_unset': 'No discount limit has been set by your admin.',
              'discount_unknown': "We don't know how much discount the customer has already had this quarter.", 'discount_cap': 'This discount would go over the quarterly limit.'}
TRI = {'Not sure': None, 'No': False, 'Yes': True}


def band(prob: float, cutoff: float) -> str:
    """high = at or above the flag cutoff; medium = at or above half of it; low otherwise."""
    return 'high' if prob >= cutoff else ('medium' if prob >= cutoff / 2 else 'low')


def one_in(prob: float) -> str:
    return 'About %d in 100 customers like this one leave.' % round(100 * prob)


def offer_text(rec: dict) -> str | None:
    if not rec.get('action'):
        return None
    d = MSG.offer_from_changes(rec['action']['name'], rec['action']['changes'])['description']
    return d[0].upper() + d[1:]


def why_approval(rec: dict) -> list[str]:
    return [RULE_PLAIN.get(f, f) for f in rec.get('fired_by', [])]


def setup_notices(b: dict) -> list[str]:
    """Things an admin has not configured, in plain words. Empty when everything is set."""
    out = []
    if b['threshold']['is_placeholder']:
        out.append('Offer cost, customer value and success rate have not been entered, so the system uses a standard cutoff for "high risk" instead of a cost-based one.')
    if b['policy'].get('max_contacts_per_month') is None or b['policy'].get('max_discount_pct_per_customer_per_quarter') is None:
        out.append('Contact and discount limits have not been set, so every offer needs a manager to approve it.')
    return out


def queue_counts() -> dict:
    c = db.summary()
    return {'waiting': c.get('pending_approval', 0), 'approved': c.get('approved', 0), 'sent_files': c.get('exported', 0) + c.get('sent', 0), 'rejected': c.get('rejected', 0),
            'to_send': c.get('exported', 0), 'escalations': c.get('escalated', 0)}


def tri(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return v if isinstance(v, bool) else str(v).strip().lower() in ('1', 'true', 'yes', 'y')


def run_batch(raw: pd.DataFrame, b: dict, limit: int = 200, progress=None) -> dict:
    """Validate, score and recommend for a CSV of customers. Unreadable rows are listed with a reason, never dropped silently.
    Optional columns: customerID, first_name, vulnerable, escalated, contacts_this_month, discount_pct_used_this_quarter (missing = unknown)."""
    missing = [c for c in D.INPUTS if c not in raw.columns]
    if missing:
        raise ValueError('The file is missing these columns: %s' % ', '.join(missing))
    raw = raw.reset_index(drop=True)
    refs = raw['customerID'].astype(str) if 'customerID' in raw.columns else pd.Series(['row%d' % (i + 1) for i in range(len(raw))])
    good, bad = [], []
    for i in range(len(raw)):
        try:
            TW.CustomerTwin(S.customer_row(raw.loc[i]), b['meta'])
            good.append(i)
        except (TW.TwinError, S.InputError, KeyError, ValueError) as e:
            bad.append({'customer': refs[i], 'problem': str(e).replace('customer is invalid: ', '')})
    if not good:
        return {'rows': pd.DataFrame(), 'unreadable': pd.DataFrame(bad), 'records': {}, 'n': len(raw), 'at_risk': 0}
    scored = S.predict(raw.loc[good, D.INPUTS], b)
    cutoff = b['threshold']['threshold']
    rows, records = [], {}
    flagged = list(scored.index[scored['flagged']])[:limit]
    state_cols = [c for c in ('vulnerable', 'escalated', 'contacts_this_month', 'discount_pct_used_this_quarter') if c in raw.columns]
    for j, i in enumerate(flagged):
        st_ = {k: (tri(raw.loc[i, k]) if k in ('vulnerable', 'escalated') else (None if pd.isna(raw.loc[i, k]) else float(raw.loc[i, k]))) for k in state_cols}
        rec = R.recommend(S.customer_row(raw.loc[i]), b, st_ or None, customer_ref=refs[i])
        first = str(raw.loc[i, 'first_name']).strip() if 'first_name' in raw.columns and not pd.isna(raw.loc[i, 'first_name']) else ''
        records[refs[i]] = (rec, first)
        rows.append({'customer': refs[i], 'chance of leaving': rec['risk']['probability'], 'risk': BAND_LABEL[band(rec['risk']['probability'], cutoff)],
                     'next step': DECISION_PLAIN[rec['decision']], 'offer': offer_text(rec) or '', 'why this customer': E.sentence(rec['shap_reasons'] or []) if rec['shap_reasons'] else '',
                     'needs approval because': ' '.join(why_approval(rec)) if rec['decision'] != 'no_action' else '', 'first name': first})
        if progress:
            progress((j + 1) / max(1, len(flagged)))
    return {'rows': pd.DataFrame(rows).sort_values('chance of leaving', ascending=False) if rows else pd.DataFrame(), 'unreadable': pd.DataFrame(bad), 'records': records,
            'n': len(raw), 'at_risk': int(scored['flagged'].sum()), 'capped': int(scored['flagged'].sum()) > len(flagged)}


def queue_batch(records: dict, b: dict, submitted_by: str | None = None) -> dict:
    """Queue every recommended customer that has a first name. Template messages only (checked by the same validator). Duplicates are skipped."""
    done, skipped_name, dup, other = 0, 0, 0, 0
    for ref, (rec, first) in records.items():
        if not rec['decision'].startswith('recommend'):
            continue
        if not first:
            skipped_name += 1
            continue
        try:
            offer = MSG.offer_from_changes(rec['action']['name'], rec['action']['changes'])
            d = MSG.draft(first, offer, rec['shap_reasons'], b['policy'], client=None)
            db.create_case(rec, offer, MSG.validate_first_name(first), d['message'], d['source'], submitted_by=submitted_by)
            db.log_message(ref, first, offer['name'], d)
            done += 1
        except db.DuplicateOpenCase:
            dup += 1
        except (db.CaseError, ValueError):
            other += 1
    return {'queued': done, 'no_first_name': skipped_name, 'already_open': dup, 'failed': other}
