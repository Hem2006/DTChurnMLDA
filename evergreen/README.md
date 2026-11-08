# EVERGREEN

A calibrated, guardrailed churn-to-retention-action pipeline with a ten-page Streamlit app (course project).
IBM Telco Customer Churn data (7,043 rows) -> calibrated XGBoost -> what-if twin -> guardrails -> one decision per customer -> human review queue.

## Run it

```bash
python -m pip install -r requirements-dev.txt     # Python 3.13; versions are pinned (the saved models only load under them)
python -m src.train                               # ~30-45 min: nested CV, calibration, 2,000-resample bootstrap CIs, MLflow
python -m src.uplift                              # SIMULATED uplift model (seconds)
python -m src.users init                          # once: creates the five logins (passwords go to config/initial_passwords.txt)
python -m streamlit run app.py                    # http://localhost:8501  (sign in first)
python -m pytest -q                               # tests (core logic + headless app)
```

`make train`, `make run`, `make test` do the same (`make train-quick` gives a fast smoke model, not for reporting).
Docker: train first so `models/` exists, then `docker build -t evergreen .` and `docker run --rm -p 8501:8501 evergreen`.
MLflow: `mlflow ui --backend-store-uri sqlite:///mlflow.db`.

Data: `data/telco.csv` is the IBM Telco CSV. The project never fabricates data; if the file is missing, training stops and tells you.

## What is where

| | |
|---|---|
| `src/train.py` | Phase 1. 17 inputs (gender and TotalCharges excluded), stratified 80/20 split (seed 42), nested CV of baseline / logistic / random forest / XGBoost / LightGBM, 5-fold calibrated XGBoost (isotonic vs Platt by out-of-fold Brier on train only), one-shot test metrics with bootstrap CIs, DeLong test, MLflow. Writes `models/`. |
| `src/threshold.py` | `t* = c / (s v)` from `config/business.yaml`; labelled F1 placeholder while unset. |
| `src/explain.py` | SHAP averaged over the calibration ensemble's 5 base pipelines, in plain English. |
| `src/twin.py` | Immutable what-if twin. Editable: Contract, add-ons, PaymentMethod, PaperlessBilling, discount 0-30%. Everything else is blocked. Low-confidence flag from a logistic cross-check. |
| `src/uplift.py` | T-learner on SIMULATED treatment (`config/uplift_sim.yaml`). Advisory. |
| `src/guardrails.py` | Deterministic: vulnerable / escalated, unknown state, policy caps, pressure language, offer-fidelity message validator. |
| `src/recommender.py` | One of `recommend` / `recommend_pending_approval` / `no_action` / `escalate_to_human`, with reasons and a full case record. |
| `src/messages.py` | LLM only phrases (if `ANTHROPIC_API_KEY` is set; model via `EVERGREEN_MESSAGE_MODEL`); every draft is validated, failures fall back to the template. |
| `src/db.py` | SQLite case queue; one open case per customer is enforced by a partial unique index. |
| `src/drift.py`, `src/fairness.py` | PSI; Fairlearn `MetricFrame`. |
| `crm/` | The pages CRM staff use: Today, Customer, Customer list, Approvals, Escalations, Outbox, Admin. Plain language, no model jargon. |
| `pages/` | Analyst tools (admin only, behind the "Analyst tools" switch): explorer, what-if lab, threshold playground, calibration, guardrail stress-tester, fairness, uplift, model arena + paper mode, batch + drift. |
| `src/auth.py`, `src/users.py` | Logins (PBKDF2 hashes, 5-attempt lockout) and roles. |

## Honest labelling (enforced in the UI and tests)

- The twin is **model sensitivity, not a causal estimate.**
- Uplift is **SIMULATED**; the assumptions are arbitrary and live in `config/uplift_sim.yaml`.
- The threshold is a **placeholder** until `config/business.yaml` has offer cost, customer value and success rate. No business value is claimed; the Cost Threshold Playground uses numbers you type and says so.
- `config/policy.yaml` ships with both caps unset, so every guardrail-governed action comes back *needs human approval*. Unknown account state is never treated as safe.
- Fairness results are screening only: small groups, no mitigation.
- Everything that is a number you may want to change is in `config/`; nothing is hidden in code.

## Known limits

- The Telco data has no treatment, no account state, no contact history and no customer names; those arrive per request or are unknown.
- Nothing is ever sent. Approved messages are appended to `outbox.csv` for someone else to send.
- Cohort and calibration conclusions come from a single seeded 80/20 split of 7,043 customers; intervals are wide.

## Logins and the approval flow

Five roles, one account each (`python -m src.users init`): **agent**, **manager**, **specialist**, **sender**, **admin**. Initial passwords are in `config/initial_passwords.txt` (git-ignored): change them in the sidebar and delete the file.

| Role | Can |
|---|---|
| agent | Look up customers, check lists, draft and submit messages; sees only their own cases |
| manager | Approve or reject (rejecting needs a reason); sees all cases and escalations. **Cannot decide a case they submitted.** |
| specialist | Closes escalations (vulnerable / open-complaint customers) with a note |
| sender | Exports approved messages to `outbox.csv` and marks them sent |
| admin | Manages users, sees setup status and activity, switches on analyst tools. Cannot submit or approve. |

Flow: agent submits -> manager approves/rejects -> sender exports and marks sent. Customers who must not be contacted skip approval and go to a specialist. Every step records who did it.
Pilot-grade login: one YAML file, sessions last until the browser tab closes. For a company roll-out, swap `auth.login()` for your SSO and keep the roles.
