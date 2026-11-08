import json
import sys
import uuid
from pathlib import Path

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)


def nid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'evergreen/' + name))


def node(name, type_, params, pos, ver=1, **extra):
    n = {'parameters': params, 'id': nid(name), 'name': name, 'type': type_, 'typeVersion': ver, 'position': pos}
    n.update(extra)
    return n


def cond(left, op_type, op, right=None):
    c = {'id': nid('c' + left), 'leftValue': left, 'operator': {'type': op_type, 'operation': op}}
    if right is not None:
        c['rightValue'] = right
    return c


def if_node(name, left, op_type, op, right, pos):
    return node(name, 'n8n-nodes-base.if', {'conditions': {'options': {'caseSensitive': True, 'leftValue': '', 'typeValidation': 'strict', 'version': 2},
                                                           'conditions': [cond(left, op_type, op, right)], 'combinator': 'and'}, 'options': {}}, pos, ver=2.2)


def set_node(name, assigns, pos):
    return node(name, 'n8n-nodes-base.set', {'assignments': {'assignments': [
        {'id': nid(name + k), 'name': k, 'value': v, 'type': 'string'} for k, v in assigns.items()]}, 'options': {}}, pos, ver=3.4)


def note(title, text, pos, w=320, h=160, color=4):
    return node('Note: ' + title, 'n8n-nodes-base.stickyNote', {'content': text, 'height': h, 'width': w, 'color': color}, pos, ver=1)


def link(conns, a, b, out=0):
    conns.setdefault(a, {'main': []})
    while len(conns[a]['main']) <= out:
        conns[a]['main'].append([])
    conns[a]['main'][out].append({'node': b, 'type': 'main', 'index': 0})


WH = "$('Webhook').first().json.body"

# ---------------------------------------------------------------- A: carry out an approved action
A = []
A.append(node('Webhook', 'n8n-nodes-base.webhook', {'httpMethod': 'POST', 'path': 'evergreen-action', 'responseMode': 'onReceived',
                                                    'options': {'rawBody': True}}, [0, 300], ver=2, webhookId=nid('webhook')))
A.append(node('Raw body to text', 'n8n-nodes-base.extractFromFile', {'operation': 'text', 'binaryPropertyName': 'data', 'options': {}}, [220, 300], ver=1))
A.append(node('Compute signature', 'n8n-nodes-base.crypto', {'action': 'hmac', 'type': 'SHA256', 'value': '={{ $json.data }}', 'dataPropertyName': 'computed',
                                                            'secret': '={{ $env.N8N_SHARED_SECRET }}', 'encoding': 'hex'}, [440, 300], ver=1))
A.append(if_node('Signature valid?', "={{ 'sha256=' + $json.computed }}", 'string', 'equals',
                 "={{ $('Webhook').first().json.headers['x-evergreen-signature'] }}", [660, 300]))
A.append(node('Ignore: bad signature', 'n8n-nodes-base.noOp', {}, [900, 520], ver=1))
A.append(set_node('Build message', {'text':
    "={{ '*Approved retention action #' + " + WH + ".action_id + '* for customer ' + " + WH + ".customer_ref + '\\n' + " + WH + ".proposed_change_text"
    " + '\\nModel risk ' + Math.round(" + WH + ".risk.before * 100) + '% -> ' + Math.round(" + WH + ".risk.after * 100) + '%'"
    " + ' (' + " + WH + ".labels.interpretation + ')\\nProposed by ' + " + WH + ".requested_by + ', approved by ' + " + WH + ".approved_by }}"}, [900, 220]))
A.append(if_node('Slack configured?', '={{ $env.SLACK_WEBHOOK_URL }}', 'string', 'notEmpty', None, [1120, 220]))
A.append(node('Post to Slack', 'n8n-nodes-base.httpRequest', {'method': 'POST', 'url': '={{ $env.SLACK_WEBHOOK_URL }}', 'sendBody': True, 'specifyBody': 'json',
                                                              'jsonBody': '={{ JSON.stringify({ text: $json.text }) }}', 'options': {}}, [1340, 120], ver=4.2,
              onError='continueErrorOutput'))
A.append(set_node('Result: posted', {'status': 'done', 'detail': 'Posted to Slack', 'external_ref': ''}, [1560, 60]))
A.append(set_node('Result: failed', {'status': 'failed', 'detail': "={{ 'Slack post failed: ' + ($json.error && $json.error.message ? $json.error.message : 'unknown error') }}",
                                     'external_ref': ''}, [1560, 220]))
A.append(set_node('Result: logged only', {'status': 'done', 'detail': 'Recorded in n8n only: SLACK_WEBHOOK_URL is not set, so nothing was posted',
                                          'external_ref': ''}, [1340, 380]))
A.append(node('Report back to Evergreen', 'n8n-nodes-base.httpRequest', {
    'method': 'POST', 'url': '={{ ' + WH + '.callback.url }}', 'sendHeaders': True,
    'headerParameters': {'parameters': [{'name': 'X-Evergreen-Secret', 'value': '={{ $env.N8N_SHARED_SECRET }}'}]},
    'sendBody': True, 'specifyBody': 'json',
    'jsonBody': '={{ JSON.stringify({ status: $json.status, detail: $json.detail, external_ref: $json.external_ref || null }) }}', 'options': {}}, [1800, 220], ver=4.2))
A.append(note('what this is', "## Evergreen: carry out an approved action\n\nThe Evergreen app calls this webhook only AFTER a named person approved the action. "
              "The call is signed (HMAC-SHA256 of the raw body, header `x-evergreen-signature`); this flow recomputes it and ignores anything that does not match.\n\n"
              "The payload has the customer REF only: no name, email or phone.", [-20, 40], 420, 200))
A.append(note('swap in your step', "## Swap in your own step\n\nOut of the box the action is posted to Slack when `SLACK_WEBHOOK_URL` is set, otherwise only recorded here "
              "(and reported back honestly as such).\n\nReplace or add nodes here: Google Sheets row, CRM task, Gmail DRAFT (not send), Notion, Jira ... "
              "Whatever you add, keep the last node reporting back with status `done` or `failed`.", [1100, 460], 430, 200, 5))
cA = {}
link(cA, 'Webhook', 'Raw body to text'); link(cA, 'Raw body to text', 'Compute signature'); link(cA, 'Compute signature', 'Signature valid?')
link(cA, 'Signature valid?', 'Build message', 0); link(cA, 'Signature valid?', 'Ignore: bad signature', 1)
link(cA, 'Build message', 'Slack configured?'); link(cA, 'Slack configured?', 'Post to Slack', 0); link(cA, 'Slack configured?', 'Result: logged only', 1)
link(cA, 'Post to Slack', 'Result: posted', 0); link(cA, 'Post to Slack', 'Result: failed', 1)
for r in ('Result: posted', 'Result: failed', 'Result: logged only'):
    link(cA, r, 'Report back to Evergreen')
wf_a = {'name': 'Evergreen - carry out approved action', 'nodes': A, 'connections': cA, 'active': False, 'settings': {'executionOrder': 'v1'},
        'id': nid('wfA'), 'tags': []}

# ---------------------------------------------------------------- B: daily risk digest
B = []
B.append(node('Every morning', 'n8n-nodes-base.scheduleTrigger', {'rule': {'interval': [{'field': 'days', 'daysInterval': 1, 'triggerAtHour': 8}]}}, [0, 200], ver=1.2))
B.append(node('Run it now (manual)', 'n8n-nodes-base.manualTrigger', {}, [0, 380], ver=1))
B.append(node('Ask Evergreen for the digest', 'n8n-nodes-base.httpRequest', {
    'method': 'GET', 'url': "={{ ($env.EVERGREEN_URL || 'http://host.docker.internal:8000') + '/automation/digest?top=10' }}",
    'sendHeaders': True, 'headerParameters': {'parameters': [{'name': 'X-API-Key', 'value': "={{ $env.EVERGREEN_API_KEY || '' }}"}]}, 'options': {}}, [240, 200], ver=4.2))
B.append(set_node('Write the digest', {'text':
    "={{ '*Evergreen daily churn digest* (' + $json.interpretation + ')\\n' + $json.summary.flagged + ' of ' + $json.summary.n + ' customers are above the alert line'"
    " + ($json.threshold_is_placeholder ? ' (PLACEHOLDER line)' : '') + '.\\nOpen actions: ' + $json.actions.pending_approval + ' awaiting approval, ' + $json.actions.sent + ' with n8n.\\n\\nMost at risk:\\n'"
    " + $json.top_at_risk.map(c => '- ' + c.customer_ref + '  ' + (c.risk >= 0.995 ? '>99' : Math.round(c.risk * 100)) + '%  ' + c.contract + ', ' + c.tenure + ' mo' + (c.has_open_action ? '  (action open)' : '')).join('\\n') }}"},
    [480, 200]))
B.append(if_node('Slack configured?', '={{ $env.SLACK_WEBHOOK_URL }}', 'string', 'notEmpty', None, [720, 200]))
B.append(node('Post digest to Slack', 'n8n-nodes-base.httpRequest', {'method': 'POST', 'url': '={{ $env.SLACK_WEBHOOK_URL }}', 'sendBody': True, 'specifyBody': 'json',
                                                                    'jsonBody': '={{ JSON.stringify({ text: $json.text }) }}', 'options': {}}, [960, 120], ver=4.2))
B.append(node('Digest stays in the execution log', 'n8n-nodes-base.noOp', {}, [960, 300], ver=1))
B.append(note('what this is', "## Evergreen: daily risk digest\n\nPulls `/automation/digest` (customer refs only) every morning and posts it to Slack when `SLACK_WEBHOOK_URL` is set. "
              "Replace the Slack step with email, Teams or a Sheet as you like. Nothing is sent to customers.", [-20, 20], 420, 150))
cB = {}
link(cB, 'Every morning', 'Ask Evergreen for the digest'); link(cB, 'Run it now (manual)', 'Ask Evergreen for the digest'); link(cB, 'Ask Evergreen for the digest', 'Write the digest'); link(cB, 'Write the digest', 'Slack configured?')
link(cB, 'Slack configured?', 'Post digest to Slack', 0); link(cB, 'Slack configured?', 'Digest stays in the execution log', 1)
wf_b = {'name': 'Evergreen - daily risk digest', 'nodes': B, 'connections': cB, 'active': False, 'settings': {'executionOrder': 'v1'},
        'id': nid('wfB'), 'tags': []}

for name, wf in (('evergreen-action.json', wf_a), ('evergreen-daily-digest.json', wf_b)):
    (OUT / name).write_text(json.dumps(wf, indent=2, ensure_ascii=False), encoding='utf8')
print('wrote', sorted(p.name for p in OUT.iterdir()))
