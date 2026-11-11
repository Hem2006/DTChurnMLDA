"""FastAPI service around the calibrated churn model and the digital twin. The bundle is loaded once at startup."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import threading
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, ConfigDict, Field, create_model

import churn_serving as cs
from app import agent as ag
from app import auth
from app import automation as au
from app import galaxy as gx
from app import guardrails as gr
from app import autopilot as ap
from app import incidents as inc
from app import message as msg
from app import pipeline as pl
from app import recommend as rec
from app import twin as tw
from app import uplift as up
from app import workflow as wf

ROOT = Path(os.environ.get('CHURN_DIR', Path(__file__).resolve().parent.parent))


def _customer_model(meta: dict) -> type[BaseModel]:
    """Pydantic model with exactly the model's input fields; allowed values come from churn_model_meta.json."""
    fields: dict[str, Any] = {}
    for c in meta['input_columns']:
        if c in meta['categorical_levels']:
            fields[c] = (Literal[tuple(meta['categorical_levels'][c])], ...)
        elif c == 'SeniorCitizen':
            fields[c] = (Literal[0, 1], ...)
        elif c == 'tenure':
            fields[c] = (int, Field(..., ge=0))
        else:
            fields[c] = (float, Field(..., ge=0, allow_inf_nan=False))
    return create_model('Customer', __config__=ConfigDict(extra='forbid'), **fields)


_META = json.loads((ROOT / 'churn_model_meta.json').read_text())
Customer = _customer_model(_META)


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customers: list[Customer] = Field(..., min_length=1, max_length=1000)  # type: ignore[valid-type]


class TwinRunRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer: Customer  # type: ignore[valid-type]
    scenarios: list[str] | None = None


class AccountState(BaseModel):
    """Operational state NOT in the model data. Unsupplied fields mean 'unknown' and force human approval; they are never assumed."""
    model_config = ConfigDict(extra='forbid')
    vulnerable: bool | None = None
    escalated: bool | None = None
    contacts_this_month: int | None = Field(None, ge=0)
    discount_pct_used_this_quarter: float | None = Field(None, ge=0, le=100)


class RecommendRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer: Customer  # type: ignore[valid-type]
    account_state: AccountState | None = None


class MessageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer: Customer  # type: ignore[valid-type]
    first_name: str = Field(..., min_length=1, max_length=40)
    account_state: AccountState | None = None
    offer: str | None = Field(None, description='Optional: must equal the offer /recommend returns; the offer is never chosen here.')
    approved_by: str | None = Field(None, max_length=80, description='Required when /recommend says recommend_pending_approval')
    customer_ref: str | None = Field(None, description='Opaque id for the log (letters, digits, - _), max 64')


class SimulateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer: Customer  # type: ignore[valid-type]
    changes: dict[str, str | int | float] = Field(..., description="e.g. {'Contract': 'One year', 'discount_pct': 5}")


class CaseRecord(BaseModel):
    """One customer for the review queue. first_name and account_state are not in the model data; omitted = unknown."""
    model_config = ConfigDict(extra='forbid')
    customer_ref: str = Field(..., pattern=r'^[A-Za-z0-9_\-]{1,64}$', description='Opaque id the CRM can join on')
    first_name: str | None = Field(None, max_length=40)
    customer: Customer  # type: ignore[valid-type]
    account_state: AccountState | None = None


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    records: list[CaseRecord] = Field(..., min_length=1, max_length=1000)


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    approved_by: str = Field(..., min_length=1, max_length=80)
    first_name: str | None = Field(None, max_length=40)


class FirstNameRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    first_name: str = Field(..., min_length=1, max_length=40)


class RejectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    rejected_by: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=1, max_length=500)


class EditMessageRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    edited_by: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1, max_length=2000)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: str = Field(..., min_length=1, max_length=40)
    pin: str = Field(..., min_length=1, max_length=80)


class SubmitRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note: str = Field(..., min_length=1, max_length=500)
    first_name: str | None = Field(None, max_length=40)


class WfApproveRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    first_name: str | None = Field(None, max_length=40)


class WfReasonRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str = Field(..., min_length=1, max_length=500)


class WfClearRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note: str | None = Field(None, max_length=500)
    message: str | None = Field(None, max_length=2000)


class WfNoteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    note: str = Field(..., min_length=1, max_length=500)


class IncidentCustomer(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer_ref: str = Field(..., pattern=r'^[A-Za-z0-9_\-]{1,64}$')
    first_name: str | None = Field(None, max_length=40)
    customer: Customer  # type: ignore[valid-type]
    account_state: AccountState | None = None


class IncidentEvent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer_ref: str = Field(..., pattern=r'^[A-Za-z0-9_\-]{1,64}$')
    kind: str
    severity: int = Field(3, ge=1, le=5)
    occurred_at: str | None = Field(None, description='ISO time; the hour is read as customer-local. Default: now.')
    work_impact: bool = False
    resolved: bool = False
    note: str | None = Field(None, max_length=300)


class IncidentSimulate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customer_ref: str = Field(..., pattern=r'^[A-Za-z0-9_\-]{1,64}$')
    storyline: str
    seed: int = 0


class CloseRecovery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    closed_by: str = Field(..., min_length=1, max_length=80)
    note: str | None = Field(None, max_length=500)


class AutopilotStart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    customers: int = Field(40, ge=8, le=60)


class AutopilotIncident(BaseModel):
    model_config = ConfigDict(extra='forbid')
    kind: str
    cell: str | None = None


class GalaxyTwinRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int = Field(..., ge=0)
    changes: dict[str, str | int | float] = Field(default_factory=dict, description="e.g. {'Contract': 'One year', 'discount_pct': 5}")


class CampaignRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    scenario: str | None = Field(None, description='A name from /twin/scenarios; its applicability rules are honoured.')
    changes: dict[str, str | int | float] | None = None
    flagged_only: bool = False
    contracts: list[str] | None = None


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: int = Field(..., ge=0, description='Galaxy customer id')
    changes: dict[str, str | int | float] = Field(..., min_length=1)
    requested_by: str = Field(..., min_length=1, max_length=80)


class ActionDecision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decided_by: str = Field(..., min_length=1, max_length=80)
    note: str | None = Field(None, max_length=500)


class ActionReject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decided_by: str = Field(..., min_length=1, max_length=80)
    reason: str = Field(..., min_length=1, max_length=500)


class ActionReport(BaseModel):
    """What n8n tells us when it has finished (or failed)."""
    model_config = ConfigDict(extra='forbid')
    status: Literal['done', 'failed']
    detail: str | None = Field(None, max_length=500)
    external_ref: str | None = Field(None, max_length=120, description='e.g. the id of the task n8n created')


class AgentRunRequest(BaseModel):
    """Start the retention agent. source='demo' samples real hold-out customers; source='rows' takes rows from an uploaded file."""
    model_config = ConfigDict(extra='forbid')
    source: Literal['demo', 'rows'] = 'demo'
    sample: int = Field(ag.DEFAULT_SAMPLE, ge=10, le=1409)
    seed: int = Field(0, ge=0)
    rows: list[dict[str, Any]] | None = Field(None, max_length=ag.MAX_ROWS)
    file_name: str | None = Field(None, max_length=200)
    max_twin: int = Field(ag.DEFAULT_MAX_TWIN, ge=1, le=500, description='How many at-risk customers the twin tests per run')
    requested_by: str | None = Field(None, max_length=80)


class ScoreRequest(BaseModel):
    """Raw rows from a user's file (CSV values arrive as strings). Each row is validated on its own; bad rows come back with a reason."""
    model_config = ConfigDict(extra='forbid')
    rows: list[dict[str, Any]] = Field(..., min_length=1, max_length=5000)
    ref_column: str = 'customerID'


GAP_FLAG = 0.10          # same value as app.fairness.GAP_FLAG (fairness needs fairlearn, a dev-only dependency; a test keeps them equal)
WEB_DIST = Path(os.environ.get('CHURN_WEB_DIST', ROOT / 'web' / 'dist'))


def require_key(x_api_key: str | None = Header(None)) -> None:
    """If CHURN_API_KEY is set, the review-queue endpoints (customer refs, names, approvals) need a matching X-API-Key header.
    Unset = open, like the rest of this API; set it before exposing the queue beyond localhost."""
    expected = os.environ.get('CHURN_API_KEY')
    if expected and not (x_api_key and hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(401, 'missing or wrong X-API-Key')


_Q = [Depends(require_key)]

PROTECTED_PREFIXES = ('/cases', '/agent/run', '/simulate', '/predict', '/recommend', '/message', '/twin', '/galaxy', '/incidents',
                      '/autopilot', '/actions', '/automation', '/outbox', '/admin', '/workflow', '/auth/me')


def current_user(authorization: str | None = Header(None)) -> dict | None:
    """The signed-in person from the bearer token. None only when no token was sent AND login is not required (legacy open mode)."""
    user = None
    if authorization and authorization.lower().startswith('bearer '):
        user = auth.verify_token(authorization[7:].strip())
        if user is None:
            raise HTTPException(401, 'your sign-in has expired; sign in again')
    if user is None and auth.login_required():
        raise HTTPException(401, 'sign in first')
    return user


def roles(*allowed: str):
    """A signed-in person must hold one of these roles. With no token and CHURN_REQUIRE_LOGIN unset, the legacy open mode applies."""
    def dep(user: dict | None = Depends(current_user)):
        if user is not None and user['role'] not in allowed:
            raise HTTPException(403, 'the %s role cannot do this; it is for: %s' % (user['role'], ', '.join(allowed)))
        return user
    return dep


def person(*allowed: str):
    """Like roles(), but a signed-in person is always required (the hand-off workflow records who did each step)."""
    def dep(user: dict | None = Depends(current_user)):
        if user is None:
            raise HTTPException(401, 'sign in first')
        if user['role'] not in allowed:
            raise HTTPException(403, 'the %s role cannot do this; it is for: %s' % (user['role'], ', '.join(allowed)))
        return user
    return dep


def legacy_write() -> None:
    if auth.login_required():
        raise HTTPException(403, 'with sign-in required, case actions go through /workflow so every step is tied to a role')


_QR = _Q + [Depends(roles('analyst', 'manager', 'compliance'))]
_QL = _Q + [Depends(legacy_write)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    bundle = cs.load_bundle(ROOT)                            # warns (and /health reports it) if library versions differ
    bundle['scenarios'] = tw.load_scenarios(ROOT / 'scenarios.yaml')
    bundle['guardrails'] = gr.load_guardrails(ROOT / 'guardrails.yaml')
    bundle['uplift'] = up.load(ROOT / up.ARTIFACT)            # SIMULATED uplift model; None if not built
    app.state.incident_cfg = inc.load_config(ROOT / 'incidents.yaml')
    app.state.auth = auth.load_users(ROOT / 'users.yaml')
    app.state.autopilot = ap.Autopilot(bundle, app.state.incident_cfg)
    app.state.autopilot_task = None
    app.state.bundle = bundle
    try:
        ag.mark_interrupted()                                 # a run left 'running' by a previous process never finishes
    except Exception:
        pass
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        app.state.version_diffs = cs.check_versions(bundle['meta'])
    yield
    app.state.autopilot.running = False
    if app.state.autopilot_task:
        app.state.autopilot_task.cancel()


app = FastAPI(title='Evergreen churn API', version='1.0', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)    # /galaxy is ~1 MB of JSON


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    # TwinError and churn_serving.prepare errors are ValueErrors with readable messages.
    return JSONResponse(status_code=422, content={'detail': str(exc)})


@app.exception_handler(inc.IncidentError)
async def incident_error_handler(request: Request, exc: inc.IncidentError):
    return JSONResponse(status_code=422, content={'detail': str(exc)})


@app.exception_handler(au.ActionNotFound)
async def action_not_found_handler(request: Request, exc: au.ActionNotFound):
    return JSONResponse(status_code=404, content={'detail': str(exc)})


@app.exception_handler(au.ActionError)
async def action_error_handler(request: Request, exc: au.ActionError):
    return JSONResponse(status_code=409, content={'detail': str(exc)})


@app.exception_handler(ag.RunNotFound)
async def run_not_found_handler(request: Request, exc: ag.RunNotFound):
    return JSONResponse(status_code=404, content={'detail': str(exc)})


@app.exception_handler(ag.AgentBusy)
async def agent_busy_handler(request: Request, exc: ag.AgentBusy):
    return JSONResponse(status_code=409, content={'detail': str(exc)})


@app.exception_handler(pl.CaseNotFound)
async def case_not_found_handler(request: Request, exc: pl.CaseNotFound):
    return JSONResponse(status_code=404, content={'detail': str(exc)})


@app.exception_handler(pl.CaseError)
async def case_error_handler(request: Request, exc: pl.CaseError):
    return JSONResponse(status_code=409, content={'detail': str(exc)})


def _bundle(request: Request) -> dict:
    return request.app.state.bundle


def _frame(customers: list) -> pd.DataFrame:
    return pd.DataFrame([c.model_dump() for c in customers])


@app.get('/health')
def health(request: Request):
    b = _bundle(request)
    threshold, source, placeholder = cs.get_threshold(b['meta'], b['config'])
    diffs = request.app.state.version_diffs
    return {'status': 'ok', 'model_loaded': True, 'model': type(b['model']).__name__,
            'calibration_method': b['meta']['calibration_method'], 'versions_match': not diffs,
            'version_differences': {k: {'saved': v[0], 'installed': v[1]} for k, v in diffs.items()},
            'threshold': threshold, 'threshold_source': source, 'threshold_is_placeholder': placeholder,
            'threshold_note': b['meta']['threshold_note'] if placeholder else None,
            'logreg_reference_loaded': b['logreg_ref'] is not None}


@app.get('/meta')
def meta(request: Request):
    """What a client needs to build the customer form and show the saved evaluation. Every number comes from churn_model_meta.json."""
    b = _bundle(request)
    m = b['meta']
    threshold, source, placeholder = cs.get_threshold(m, b['config'])
    cfg = b['config'] or {}
    keys = ('test_metrics', 'test_metrics_ci95', 'bootstrap_resamples', 'test_auc_baseline_contract_tenure', 'calibration_test',
            'nested_cv_auc', 'repeated_cv_auc', 'subgroups', 'selected_model', 'calibration_method', 'feature_set_chosen', 'dropped_columns',
            'ablation_mean_auc', 'explainer_top3_overlap', 'explainer_model', 'versions', 'seed', 'threshold_source')
    return {'input_columns': m['input_columns'], 'numeric_columns': m['numeric_columns'], 'categorical_levels': m['categorical_levels'],
            'addons': tw.ADDONS, 'no_internet': tw.NO_INTERNET, 'no_phone': tw.NO_PHONE, 'max_discount_pct': tw.MAX_DISCOUNT_PCT,
            'threshold': threshold, 'threshold_source': source, 'threshold_is_placeholder': placeholder,
            'threshold_note': m['threshold_note'] if placeholder else None,
            'business_numbers_set': {k: cfg.get(k) is not None for k in ('offer_cost', 'customer_value', 'success_rate')},
            'uplift_built': b['uplift'] is not None,
            'labels': {'twin': tw.INTERPRETATION, 'uplift': up.NOTE},
            'logreg_reference_test_auc': (m.get('logreg_reference') or {}).get('test_roc_auc'),
            'fairness_gap_flag': GAP_FLAG, 'queue_key_required': bool(os.environ.get('CHURN_API_KEY')),
            'evaluation': {k: m.get(k) for k in keys}}


@app.get('/model/card')
def model_card():
    """The model card and fairness report as Markdown text (for the data-science view; the business screens never show them)."""
    read = lambda name: (ROOT / name).read_text(encoding='utf-8') if (ROOT / name).exists() else None
    return {'model_card': read('MODEL_CARD.md'), 'fairness_report': read('fairness_report.md')}


@app.post('/score')
def score(req: ScoreRequest, request: Request):
    """Score a user's file row by row. Rows that fail validation are returned with the reason, never silently dropped."""
    b = _bundle(request)
    m = b['meta']
    cols, ints = m['input_columns'], ('SeniorCitizen', 'tenure')
    missing = [c for c in cols if c not in req.rows[0]]
    if missing:
        raise ValueError('Missing required columns: %s' % missing)
    good, refs, rejected = [], [], []
    for i, raw in enumerate(req.rows):
        ref = str(raw.get(req.ref_column) or 'row %d' % (i + 1))
        try:
            row = {}
            for c in cols:
                v = raw.get(c)
                if c in m['numeric_columns']:
                    v = float(str(v).strip())
                    if c in ints:
                        if not v.is_integer():
                            raise ValueError('%s must be a whole number, got %r' % (c, raw.get(c)))
                        v = int(v)
                else:
                    v = '' if v is None else str(v).strip()
                row[c] = v
            tw.CustomerTwin(row, m)                          # coherence + the serving validator
        except (ValueError, TypeError) as e:
            rejected.append({'ref': ref, 'reason': str(e)})
            continue
        good.append(row)
        refs.append(ref)
    results = []
    if good:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            pred = cs.predict(pd.DataFrame(good), b)
        results = [{'ref': ref, 'churn_probability': float(p), 'flagged': bool(f), 'customer': row}
                   for ref, p, f, row in zip(refs, pred['churn_probability'], pred['flagged'], good)]
    threshold, source, placeholder = cs.get_threshold(m, b['config'])
    return {'results': results, 'rejected': rejected, 'threshold': threshold, 'threshold_source': source, 'threshold_is_placeholder': placeholder}


@app.post('/predict')
def predict(req: PredictRequest, request: Request):
    b = _bundle(request)
    df = _frame(req.customers)
    cs.prepare(df, b['meta'])                                # same validator as offline scoring; ValueError -> 422
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        pred = cs.predict(df, b)
        reasons = cs.top_reasons(df, b)                      # flagged customers only
    out = []
    for i in pred.index:
        r = pred.loc[i]
        out.append({'index': int(i), 'churn_probability': float(r['churn_probability']), 'flagged': bool(r['flagged']),
                    'threshold': float(r['threshold']), 'threshold_source': r['threshold_source'],
                    'threshold_is_placeholder': bool(r['threshold_is_placeholder']),
                    'reasons': reasons.loc[i, 'reasons'] if i in reasons.index else None})
    return {'results': out, 'reasons_note': 'reasons are SHAP log-odds pushes of the base XGBoost score, flagged customers only'}


@app.post('/explain')
def explain(req: TwinRunRequest, request: Request):
    """All SHAP contributions for one customer (log-odds of the base XGBoost score, averaged over the 5 calibration folds)."""
    b = _bundle(request)
    df = _frame([req.customer])
    cs.prepare(df, b['meta'])
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        contrib, _, _ = cs.explain(df, b)
    c = contrib.iloc[0].sort_values(ascending=False)
    return {'contributions': [{'feature': k, 'value': req.customer.model_dump()[k], 'log_odds_push': float(v)} for k, v in c.items()],
            'note': 'positive pushes toward churn; log-odds of the base XGBoost score averaged over the 5 calibration folds; describes the model, not a cause'}


@app.get('/twin/scenarios')
def scenarios(request: Request):
    b = _bundle(request)
    return {'interpretation': tw.INTERPRETATION,
            'scenarios': [{'name': s['name'], 'description': s['description'], 'changes': s['changes'],
                           'cost_per_customer': tw._cost(s['name'], s['cost_per_customer'], b['config']),
                           'requires': s['requires'], 'not_applicable_if': s['not_applicable_if']}
                          for s in b['scenarios'].values()]}


@app.post('/twin/run')
def twin_run(req: TwinRunRequest, request: Request):
    b = _bundle(request)
    return tw.run_scenarios(req.customer.model_dump(), b, req.scenarios)


@app.post('/simulate')
def simulate(req: SimulateRequest, request: Request):
    return tw.simulate(req.customer.model_dump(), _bundle(request), req.changes)


@app.post('/recommend')
def recommend(req: RecommendRequest, request: Request):
    state = req.account_state.model_dump() if req.account_state else None
    return rec.recommend(req.customer.model_dump(), _bundle(request), state)


@app.post('/message')
def message(req: MessageRequest, request: Request):
    """Draft a retention message for the offer /recommend has already approved. The LLM only phrases it; it never picks the offer."""
    first = msg.validate_first_name(req.first_name)
    if req.customer_ref is not None and not msg.REF_RE.match(req.customer_ref):
        raise ValueError('customer_ref must be 1-64 characters: letters, digits, - or _')
    b = _bundle(request)
    state = req.account_state.model_dump() if req.account_state else None
    r = rec.recommend(req.customer.model_dump(), b, state)
    if r['decision'] not in ('recommend', 'recommend_pending_approval'):
        raise HTTPException(409, {'error': 'no message: nothing to offer', 'decision': r['decision'], 'reasons': r['reasons']})
    action = r['action']
    if req.offer is not None and req.offer != action['name']:
        raise HTTPException(409, {'error': 'offer does not match the approved recommendation (the offer is never chosen by /message)',
                                  'requested': req.offer, 'approved': action['name']})
    if r['decision'] == 'recommend_pending_approval' and not req.approved_by:
        raise HTTPException(409, {'error': 'this offer needs human approval before a message is drafted; supply approved_by',
                                  'approvals_needed': action['guardrails']['approvals_needed']})
    offer = msg.offer_from_action(action)
    d = msg.draft_message(first, offer, r['shap_reasons'], b['guardrails'], client=getattr(request.app.state, 'llm_client', 'auto'))
    log_id = msg.log_message({'customer_ref': req.customer_ref, 'first_name': first, 'offer': offer, 'shap_reasons': r['shap_reasons'],
                              'message': d['message'], 'source': d['source'], 'model': d['model'], 'rejected_reason': d['rejected_reason'],
                              'rejected_text': d['rejected_text'], 'decision': r['decision'], 'guardrail_status': action['guardrails']['status'],
                              'approved_by': req.approved_by})
    return {'message': d['message'], 'source': d['source'], 'model': d['model'], 'rejected_reason': d['rejected_reason'], 'offer': offer,
            'decision': r['decision'], 'guardrail_status': action['guardrails']['status'], 'log_id': log_id,
            'interpretation': tw.INTERPRETATION}


# ------------------------------------------------------------------ customer galaxy (see app/galaxy.py)
@app.get('/galaxy')
def galaxy(request: Request):
    """The hold-out customers laid out in space with their calibrated churn probability (built once, then cached on the bundle)."""
    return gx.build(_bundle(request))['payload']


@app.post('/galaxy/twin')
def galaxy_twin(req: GalaxyTwinRequest, request: Request):
    return gx.twin_node(_bundle(request), req.id, req.changes)


@app.post('/galaxy/campaign')
def galaxy_campaign(req: CampaignRequest, request: Request):
    if (req.scenario is None) == (req.changes is None):
        raise ValueError('send exactly one of scenario or changes')
    return gx.campaign(_bundle(request), scenario=req.scenario, changes=req.changes, flagged_only=req.flagged_only, contracts=req.contracts)


# ------------------------------------------------------------------ actions + n8n (see app/automation.py)
def require_n8n_secret(x_evergreen_secret: str | None = Header(None)) -> None:
    """n8n's report back must carry the shared secret (N8N_SHARED_SECRET). Without a configured secret nobody can report."""
    if not au.secret_ok(x_evergreen_secret):
        raise HTTPException(401, 'missing or wrong %s (and N8N_SHARED_SECRET must be set on the server)' % au.SECRET_HEADER)


@app.get('/automation/status', dependencies=_Q)
def automation_status():
    return au.status()


@app.get('/automation/digest', dependencies=_Q)
def automation_digest(request: Request, top: int = 10):
    """For a scheduled n8n report: most at-risk customers (refs only) and the state of the action queue."""
    return au.digest(_bundle(request), top=top)


@app.get('/actions', dependencies=_Q)
def actions_list(status: str | None = None, limit: int = 200):
    return {'summary': au.summary(), 'automation': au.status(), 'actions': au.list_actions(status, limit=max(1, min(limit, 1000)))}


@app.post('/actions', dependencies=_Q)
def actions_propose(req: ActionProposal, request: Request):
    return au.propose(_bundle(request), req.id, req.changes, req.requested_by)


@app.get('/actions/{action_id}', dependencies=_Q)
def actions_get(action_id: int):
    return au.get_action(action_id)


@app.post('/actions/{action_id}/approve', dependencies=_Q)
def actions_approve(action_id: int, req: ActionDecision):
    return au.approve(action_id, req.decided_by, req.note)


@app.post('/actions/{action_id}/reject', dependencies=_Q)
def actions_reject(action_id: int, req: ActionReject):
    return au.reject(action_id, req.decided_by, req.reason)


@app.post('/actions/{action_id}/retry', dependencies=_Q)
def actions_retry(action_id: int):
    return au.retry(action_id)


@app.post('/actions/{action_id}/callback', dependencies=[Depends(require_n8n_secret)])
def actions_callback(action_id: int, req: ActionReport):
    return au.callback(action_id, req.status, req.detail, req.external_ref)


# ------------------------------------------------------------------ review queue (see app/pipeline.py)


def _llm(request: Request):
    return getattr(request.app.state, 'llm_client', 'auto')


@app.post('/cases/ingest', dependencies=_QL)
def cases_ingest(req: IngestRequest, request: Request):
    """Score customers and queue the outcome. Cases the guardrails clear get a validated message; the rest wait for a person."""
    recs = [{'customer_ref': r.customer_ref, 'first_name': r.first_name, 'customer': r.customer.model_dump(),
             'account_state': r.account_state.model_dump() if r.account_state else None, 'error': None} for r in req.records]
    return pl.ingest(recs, _bundle(request), client=_llm(request), source='api')


@app.get('/cases', dependencies=_QR)
def cases_list(status: str | None = None, limit: int = 200):
    return {'summary': pl.summary(), 'cases': pl.list_cases(status, limit=max(1, min(limit, 1000)))}


@app.get('/cases/{case_id}', dependencies=_QR)
def cases_get(case_id: int):
    return pl.get_case(case_id)


@app.post('/cases/{case_id}/approve', dependencies=_QL)
def cases_approve(case_id: int, req: ApproveRequest, request: Request):
    return pl.approve(case_id, req.approved_by, _bundle(request)['guardrails'], first_name=req.first_name, client=_llm(request))


@app.post('/cases/{case_id}/first-name', dependencies=_QL)
def cases_first_name(case_id: int, req: FirstNameRequest, request: Request):
    return pl.provide_first_name(case_id, req.first_name, _bundle(request)['guardrails'], client=_llm(request))


@app.post('/cases/{case_id}/message', dependencies=_QL)
def cases_edit_message(case_id: int, req: EditMessageRequest, request: Request):
    return pl.edit_message(case_id, req.message, _bundle(request)['guardrails'], edited_by=req.edited_by)


@app.post('/cases/{case_id}/reject', dependencies=_QL)
def cases_reject(case_id: int, req: RejectRequest):
    return pl.reject(case_id, req.rejected_by, req.reason)


@app.post('/outbox/export', dependencies=_QL)
def outbox_export():
    """Return the ready messages and mark them exported. Nothing is sent: the CRM sends them, matching customer_ref to contacts."""
    rows = pl.export_outbox()
    return {'count': len(rows), 'messages': rows}


# ------------------------------------------------------------------ sign-in and the four-role hand-off (see app/auth.py, app/workflow.py)
@app.middleware('http')
async def login_gate(request: Request, call_next):
    """With CHURN_REQUIRE_LOGIN=1 every data endpoint needs a valid token (n8n's callback and digest keep their own secrets)."""
    p = request.url.path
    if (auth.login_required() and p.startswith(PROTECTED_PREFIXES) and not (p.startswith('/actions/') and p.endswith('/callback'))
            and p != '/automation/digest'):
        h = request.headers.get('authorization', '')
        if not (h.lower().startswith('bearer ') and auth.verify_token(h[7:].strip())):
            return JSONResponse(status_code=401, content={'detail': 'sign in first'})
    return await call_next(request)


@app.exception_handler(wf.SeparationError)
async def separation_handler(request: Request, exc: wf.SeparationError):
    return JSONResponse(status_code=403, content={'detail': str(exc)})


def _accounts(request: Request) -> dict:
    return request.app.state.auth


@app.get('/auth/accounts')
def auth_accounts(request: Request):
    return {'accounts': auth.public_accounts(_accounts(request)), 'login_required': auth.login_required(), 'demo': True}


@app.post('/auth/login')
def auth_login(req: LoginRequest, request: Request):
    try:
        out = auth.login(_accounts(request), req.user_id, req.pin)
    except auth.AuthError as e:
        wf.log_auth('login_failed', req.user_id[:40], None)
        raise HTTPException(e.status, str(e))
    wf.log_auth('login', out['user']['name'], out['user']['role'])
    return out


@app.get('/auth/me')
def auth_me(user: dict = Depends(person('analyst', 'manager', 'compliance', 'admin'))):
    return user


_W = _Q


@app.get('/workflow/board', dependencies=_W)
def wf_board(user: dict = Depends(person('analyst', 'manager', 'compliance'))):
    return wf.board()


@app.get('/workflow/cases/{case_id}', dependencies=_W)
def wf_case(case_id: int, user: dict = Depends(person('analyst', 'manager', 'compliance'))):
    d = pl.get_case(case_id)
    d['stage'] = wf.stage_of(d)
    return d


@app.post('/workflow/cases/{case_id}/submit', dependencies=_W)
def wf_submit(case_id: int, req: SubmitRequest, request: Request, user: dict = Depends(person('analyst'))):
    return wf.submit(case_id, user, req.note, _bundle(request)['guardrails'], first_name=req.first_name)


@app.post('/workflow/cases/{case_id}/first-name', dependencies=_W)
def wf_first_name(case_id: int, req: FirstNameRequest, request: Request, user: dict = Depends(person('analyst', 'manager'))):
    return wf.name_it(case_id, user, req.first_name, _bundle(request)['guardrails'], client=_llm(request))


@app.post('/workflow/cases/{case_id}/send-back', dependencies=_W)
def wf_send_back(case_id: int, req: WfReasonRequest, user: dict = Depends(person('manager'))):
    return wf.send_back(case_id, user, req.reason)


@app.post('/workflow/cases/{case_id}/approve', dependencies=_W)
def wf_approve(case_id: int, req: WfApproveRequest, request: Request, user: dict = Depends(person('manager'))):
    return wf.approve(case_id, user, _bundle(request)['guardrails'], first_name=req.first_name, client=_llm(request))


@app.post('/workflow/cases/{case_id}/reject', dependencies=_W)
def wf_reject(case_id: int, req: WfReasonRequest, user: dict = Depends(person('manager', 'compliance'))):
    return wf.reject(case_id, user, req.reason)


@app.post('/workflow/cases/{case_id}/clear', dependencies=_W)
def wf_clear(case_id: int, req: WfClearRequest, request: Request, user: dict = Depends(person('compliance'))):
    return wf.clear(case_id, user, req.note, _bundle(request)['guardrails'], message=req.message)


@app.post('/workflow/cases/{case_id}/care-handoff', dependencies=_W)
def wf_care(case_id: int, req: WfNoteRequest, user: dict = Depends(person('compliance'))):
    return wf.care_handoff(case_id, user, req.note)


@app.post('/workflow/export', dependencies=_W)
def wf_export(user: dict = Depends(person('compliance'))):
    rows = wf.export(user)
    return {'count': len(rows), 'messages': rows}


@app.get('/admin/overview', dependencies=_W)
def admin_overview(request: Request, user: dict = Depends(person('admin'))):
    cfg = yaml.safe_load((ROOT / 'config.yaml').read_text(encoding='utf8')) or {}
    gcfg = _bundle(request)['guardrails']
    labels = [('offer_cost', 'Cost of one retention offer', cfg), ('customer_value', 'Value kept when a customer stays', cfg),
              ('success_rate', 'Share of would-be leavers an offer keeps', cfg), ('twin_effect_realization', 'Twin effect scaling', cfg),
              ('scenario_costs', 'Cost per option', cfg), ('max_discount_pct_per_customer_per_quarter', 'Discount limit per quarter (%)', gcfg),
              ('max_contacts_per_month', 'Contacts allowed per month', gcfg)]
    numbers = [{'key': k, 'label': t, 'set': src.get(k) is not None, 'value': src.get(k)} for k, t, src in labels]
    board = wf.board()
    return {'numbers': numbers, 'counts': board['counts'], 'stages': board['stages'], 'total_cases': len(board['cases']),
            'duty_violations': wf.duty_violations(), 'health': health(request), 'recent_runs': ag.list_runs(limit=5),
            'people': auth.public_accounts(_accounts(request)),
            'settings': {'login_required': auth.login_required(), 'access_key_set': bool(os.environ.get('CHURN_API_KEY')),
                         'assistant_key_set': bool(os.environ.get('ANTHROPIC_API_KEY')),
                         'automation_configured': bool(os.environ.get('N8N_WEBHOOK_URL') and os.environ.get('N8N_SHARED_SECRET'))}}


@app.get('/admin/audit', dependencies=_W)
def admin_audit(limit: int = 200, user: dict = Depends(person('admin'))):
    return {'events': wf.audit(max(1, min(limit, 1000)))}


# ------------------------------------------------------------------ retention agent (see app/agent.py)
@app.get('/agent/stages')
def agent_stages():
    return {'stages': [{'key': k, 'title': t, 'what': w, 'kind': kind} for k, t, w, kind in ag.STAGES], 'demo_note': ag.DEMO_NOTE,
            'twin_label': tw.INTERPRETATION, 'default_sample': ag.DEFAULT_SAMPLE, 'default_max_twin': ag.DEFAULT_MAX_TWIN}


@app.post('/agent/run', dependencies=_Q + [Depends(roles('analyst'))])
def agent_run(req: AgentRunRequest, request: Request):
    """Start a run in the background; poll GET /agent/runs/{id}. Nothing is sent to any customer."""
    b = _bundle(request)
    if req.source == 'demo':
        records, source = ag.demo_records(b['meta'], sample=req.sample, seed=req.seed), 'demo: %d hold-out customers (seed %d)' % (req.sample, req.seed)
    else:
        if not req.rows:
            raise ValueError("source='rows' needs rows")
        records, source = ag.rows_records(req.rows, b['meta']), 'file: %s (%d rows)' % (req.file_name or 'upload', len(req.rows))
    return ag.start(records, b, client=_llm(request), source=source, requested_by=req.requested_by, max_twin=req.max_twin)


@app.get('/agent/runs', dependencies=_QR)
def agent_runs(limit: int = 20):
    return {'runs': ag.list_runs(limit=max(1, min(limit, 200)))}


@app.get('/agent/runs/{run_id}', dependencies=_QR)
def agent_run_get(run_id: int):
    return ag.get_run(run_id)


@app.get('/agent/runs/{run_id}/customers', dependencies=_QR)
def agent_run_customers(run_id: int, request: Request, scope: str = 'all'):
    return {'customers': ag.run_customers(run_id, scope=scope, bundle=_bundle(request))}


@app.get('/agent/runs/{run_id}/customers/{ref}', dependencies=_QR)
def agent_run_customer(run_id: int, ref: str, request: Request):
    return ag.customer_detail(run_id, ref, bundle=_bundle(request))


# ------------------------------------------------------------------ service incidents (see app/incidents.py)
def _icfg(request: Request) -> dict:
    return request.app.state.incident_cfg


@app.get('/incidents/config', dependencies=_Q)
def incidents_config(request: Request):
    c = _icfg(request)
    return {'label': inc.SIMULATED, 'note': inc.NOTE, 'event_kinds': {k: v['weight'] for k, v in c['event_kinds'].items()},
            'levels': c['levels'], 'trigger_level': c['trigger_level'], 'half_life_days': c['half_life_days'],
            'storylines': {k: v['description'] for k, v in c['storylines'].items()}, 'segments': list(inc.SEGMENTS)}


@app.post('/incidents/customers', dependencies=_Q)
def incidents_register(req: IncidentCustomer, request: Request):
    state = req.account_state.model_dump() if req.account_state else None
    return inc.register_customer(req.customer_ref, req.customer.model_dump(), _bundle(request), first_name=req.first_name, account_state=state)


@app.get('/incidents/customers', dependencies=_Q)
def incidents_customers(request: Request):
    return {'label': inc.SIMULATED, 'customers': inc.list_customers(_bundle(request), _icfg(request))}


@app.get('/incidents/customers/{ref}', dependencies=_Q)
def incidents_customer(ref: str, request: Request):
    return inc.evaluate(ref, _bundle(request), _icfg(request))


@app.post('/incidents/events', dependencies=_Q)
def incidents_event(req: IncidentEvent, request: Request):
    """Log one event, then run the self-healing check: at or past the trigger level a recovery opens (or refreshes)."""
    inc.log_event(req.customer_ref, req.kind, _icfg(request), severity=req.severity, occurred_at=req.occurred_at,
                  work_impact=req.work_impact, resolved=req.resolved, note=req.note)
    return inc.process(req.customer_ref, _bundle(request), _icfg(request))


@app.post('/incidents/simulate', dependencies=_Q)
def incidents_simulate(req: IncidentSimulate, request: Request):
    """Play a canned storyline into the customer's log as SIMULATED events, then run the self-healing check."""
    inc.simulate(req.customer_ref, req.storyline, _icfg(request), seed=req.seed)
    return inc.process(req.customer_ref, _bundle(request), _icfg(request))


@app.post('/incidents/customers/{ref}/reset-simulated', dependencies=_Q)
def incidents_reset(ref: str, request: Request):
    n = inc.reset_simulated(ref)
    return {'removed': n, **inc.process(ref, _bundle(request), _icfg(request))}


@app.post('/incidents/events/{event_id}/resolve', dependencies=_Q)
def incidents_resolve(event_id: int):
    inc.resolve_event(event_id)
    return {'resolved': event_id}


@app.get('/incidents/recoveries', dependencies=_Q)
def incidents_recoveries(status: str | None = None):
    return {'label': inc.SIMULATED, 'recoveries': inc.list_recoveries(status)}


@app.post('/incidents/recoveries/{recovery_id}/close', dependencies=_Q)
def incidents_close(recovery_id: int, req: CloseRecovery):
    return inc.close_recovery(recovery_id, req.closed_by, req.note)


# ------------------------------------------------------------------ autopilot (see app/autopilot.py)
TICK_SECONDS = float(os.environ.get('CHURN_AUTOPILOT_TICK', '2.5'))


async def _autopilot_loop(pilot: ap.Autopilot) -> None:
    while pilot.running:
        try:
            await asyncio.to_thread(pilot.tick)
        except Exception:          # one bad beat must not stop the demo; the next tick tries again
            pass
        await asyncio.sleep(TICK_SECONDS)


@app.post('/autopilot/start', dependencies=_Q)
async def autopilot_start(req: AutopilotStart, request: Request):
    pilot = request.app.state.autopilot
    n = len(pilot.snap)
    await asyncio.to_thread(pilot.seed, req.customers)
    if not n:
        threading.Thread(target=pilot.prewarm, daemon=True).start()      # digital twin runs ahead of the first incident
    pilot.running = True
    task = request.app.state.autopilot_task
    if task is None or task.done():
        request.app.state.autopilot_task = asyncio.create_task(_autopilot_loop(pilot))
    return await asyncio.to_thread(pilot.state)


@app.post('/autopilot/stop', dependencies=_Q)
async def autopilot_stop(request: Request):
    pilot = request.app.state.autopilot
    pilot.running = False
    return await asyncio.to_thread(pilot.state)


@app.post('/autopilot/reset', dependencies=_Q)
async def autopilot_reset(request: Request):
    return await asyncio.to_thread(request.app.state.autopilot.reset)


@app.post('/autopilot/incident', dependencies=_Q)
async def autopilot_incident(req: AutopilotIncident, request: Request):
    return await asyncio.to_thread(request.app.state.autopilot.inject, req.kind, cell=req.cell)


@app.get('/autopilot/state', dependencies=_Q)
async def autopilot_state(request: Request):
    return await asyncio.to_thread(request.app.state.autopilot.state)


# ------------------------------------------------------------------ web app (React build in web/dist); mounted last so API routes win
if (WEB_DIST / 'index.html').exists():
    from fastapi.staticfiles import StaticFiles
    app.mount('/', StaticFiles(directory=WEB_DIST, html=True), name='web')
