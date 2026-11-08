# n8n: making the galaxy do something

Evergreen decides and explains; **n8n carries things out**. The loop:

```
twin (galaxy)  ->  Propose this action  ->  a named person approves it in Actions
      ->  Evergreen sends a SIGNED webhook to n8n  ->  n8n does the work
      ->  n8n reports back  ->  the customer's ring in the galaxy turns green (done) or pink (failed)
```

Rules the code enforces (`app/automation.py`, tested in `tests/test_automation.py`):

- Nothing reaches n8n before a named person approves. Rejected actions never leave the app.
- Numbers in a proposal are recomputed on the server from the served model; they carry the "model sensitivity, not a causal estimate" label.
- n8n gets the customer **ref only** (plus contract, tenure, internet type, monthly charge). No name, email or phone.
- The webhook URL comes from the environment, never from a request, and only `http(s)` is followed.
- Outbound calls are signed (HMAC-SHA256 of the raw body, header `X-Evergreen-Signature`). The flow recomputes the signature and ignores anything that does not match. n8n's report back must carry the same secret (`X-Evergreen-Secret`), and is accepted once, only for an action that was actually sent.
- n8n never chooses the offer. It receives the approved edit. It sends nothing to customers out of the box.

## Two workflows (in `workflows/`)

| File | Trigger | What it does |
|---|---|---|
| `evergreen-action.json` | Webhook from Evergreen after approval | Verifies the signature, posts the approved action to Slack if `SLACK_WEBHOOK_URL` is set (otherwise only records it), then reports `done` / `failed` back. Swap the Slack step for a CRM task, Google Sheet row, Gmail **draft**, Jira ... |
| `evergreen-daily-digest.json` | Every day at 08:00 | Reads `GET /automation/digest` (the most at-risk customers, refs only, plus the action queue) and posts it to Slack. |

## Set up

1. **Pick a secret** and run n8n. With Docker:
   ```bash
   cd n8n
   cp .env.example .env          # set N8N_SHARED_SECRET (and optionally SLACK_WEBHOOK_URL)
   docker compose up -d          # http://localhost:5678
   ```
   Without Docker, any n8n >= 2 on Node 24 works, if these variables are set where n8n starts:
   `N8N_SHARED_SECRET`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, optionally `SLACK_WEBHOOK_URL`, `EVERGREEN_URL` (default `http://host.docker.internal:8000`).
2. **Import the workflows.** n8n UI -> Workflows -> Import from file (both files), or
   `docker compose exec n8n n8n import:workflow --separate --input=/workflows`.
3. **Activate** `Evergreen - carry out approved action` (the toggle at the top right). The production webhook is
   `http://localhost:5678/webhook/evergreen-action`. Activate the digest too if you want it.
4. **Start Evergreen with the same secret and the webhook URL:**
   ```bash
   N8N_WEBHOOK_URL=http://localhost:5678/webhook/evergreen-action \
   N8N_SHARED_SECRET=<same secret> \
   EVERGREEN_PUBLIC_URL=http://host.docker.internal:8000 \
   .venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```
   `EVERGREEN_PUBLIC_URL` is how **n8n** reaches Evergreen for the report back (`http://host.docker.internal:8000` when n8n is in Docker, `http://127.0.0.1:8000` when it is not; see below).
   On PowerShell use `$env:N8N_WEBHOOK_URL = '...'` first.
5. In the app: click a customer, edit the twin, **Propose this action**, then open **Actions**, enter your name and **Approve**.

**Good to know**
- Docker: the API must listen where the container can reach it, so start uvicorn with `--host 0.0.0.0` (the default `127.0.0.1` is invisible from inside Docker). That exposes the API to your network, so set `CHURN_API_KEY` too.
- Not Docker: use `http://127.0.0.1:8000` for `EVERGREEN_PUBLIC_URL`, not `localhost`. n8n resolves `localhost` to IPv6 (`::1`) first, and uvicorn listens on IPv4 only, so the report back is refused.
- The first webhook after n8n starts can be slow. The app waits 15 s (`N8N_TIMEOUT`). If it still times out the action shows "Could not reach n8n", but n8n may have received it anyway; its report is accepted, and **Send to n8n again** can run the workflow twice, so make custom steps safe to repeat (or dedupe on `action_id`).
- If an action stays on "Sent to n8n", n8n could not report back. Open the execution in n8n (Executions tab) and check `EVERGREEN_PUBLIC_URL`.

Without `N8N_WEBHOOK_URL` and `N8N_SHARED_SECRET` the app still works: approved actions wait (status "Approved · waiting for n8n") and the Actions panel says what is missing. **Send to n8n again** retries once n8n is up.

## Using n8n Cloud or a remote n8n

n8n has to reach Evergreen for the report back, so `EVERGREEN_PUBLIC_URL` must be a URL n8n can open (a tunnel such as `cloudflared` for a local app, or the deployed address). Use https, and set `CHURN_API_KEY` on the API to protect `/actions*`, `/automation/*` and the rest of the review endpoints. The report-back endpoint is protected by `N8N_SHARED_SECRET` only.

## Extending it

Put your own nodes between **Build message** and **Report back to Evergreen**. Keep the last node reporting `status: done|failed` with a truthful `detail`; `external_ref` can carry the id of whatever n8n created (a ticket, a row), and the Actions panel shows it.
