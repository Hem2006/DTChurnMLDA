import { ArrowRight, CheckCircle2, FileSpreadsheet, FlaskConical, ShieldCheck, UserSearch } from 'lucide-react'
import { useApp } from '../state.jsx'
import { Badge, Card, Hl, Stat } from '../components/ui.jsx'
import { num, pct } from '../lib.js'

function HeroArt({ auc }) {
  return (
    <svg className="art" viewBox="0 0 520 380" width="100%" role="img" aria-label="Illustration of a retention dashboard">
      <defs>
        <filter id="sh" x="-20%" y="-20%" width="140%" height="140%"><feDropShadow dx="0" dy="14" stdDeviation="16" floodColor="#021d3d" floodOpacity=".45" /></filter>
      </defs>
      <circle cx="420" cy="70" r="60" fill="#4F9CF9" opacity=".18" />
      <rect x="40" y="40" width="400" height="280" rx="22" fill="#fff" filter="url(#sh)" />
      <rect x="40" y="40" width="400" height="44" rx="22" fill="#EEF5FF" />
      <rect x="40" y="62" width="400" height="22" fill="#EEF5FF" />
      <circle cx="66" cy="62" r="6" fill="#FFE492" /><circle cx="86" cy="62" r="6" fill="#4F9CF9" /><circle cx="106" cy="62" r="6" fill="#c9d8ee" />
      <circle cx="140" cy="182" r="62" fill="none" stroke="#edf1f7" strokeWidth="16" />
      <circle cx="140" cy="182" r="62" fill="none" stroke="#4F9CF9" strokeWidth="16" strokeLinecap="round" strokeDasharray="250 400" transform="rotate(-90 140 182)" />
      <text x="140" y="190" textAnchor="middle" fontSize="26" fontWeight="800" fill="#043873">Risk</text>
      {[0, 1, 2, 3, 4].map(i => (
        <g key={i}>
          <rect x="236" y={118 + i * 30} width="170" height="10" rx="5" fill="#edf1f7" />
          <rect x="236" y={118 + i * 30} width={[150, 112, 86, 60, 38][i]} height="10" rx="5" fill={i < 2 ? '#043873' : '#4F9CF9'} />
        </g>
      ))}
      <rect x="236" y="270" width="96" height="30" rx="9" fill="#4F9CF9" />
      <text x="284" y="290" textAnchor="middle" fontSize="12" fontWeight="700" fill="#fff">Approve</text>
      <rect x="342" y="270" width="64" height="30" rx="9" fill="#fff" stroke="#d5ddea" />
      <g filter="url(#sh)">
        <rect x="330" y="250" width="170" height="96" rx="18" fill="#FFE492" />
        <text x="350" y="282" fontSize="12" fontWeight="700" fill="#5a4100">GUARDRAILS</text>
        <text x="350" y="306" fontSize="17" fontWeight="800" fill="#043873">Checked in code</text>
        <text x="350" y="327" fontSize="11" fill="#5a4100">named person approves</text>
      </g>
 {auc != null && <g filter="url(#sh)">
        <rect x="0" y="262" width="150" height="74" rx="16" fill="#043873" stroke="rgba(255,255,255,.2)" />
        <text x="18" y="292" fontSize="11" fill="#a9c6ef" fontWeight="600">TEST ROC-AUC</text>
        <text x="18" y="318" fontSize="22" fontWeight="800" fill="#fff">{auc}</text>
      </g>}
    </svg>
  )
}

function MiniRows({ rows }) {
  return (
    <div className="card" style={{ width: '100%', maxWidth: 380 }}>
      <div className="stack-sm">
        {rows.map(([name, w, tone]) => (
          <div key={name} className="row spread small">
            <span>{name}</span>
            <span style={{ width: 140 }} className={`meter ${tone === 'hot' ? 'hot' : ''}`}><i style={{ width: w + '%' }} /></span>
          </div>
        ))}
      </div>
    </div>
  )
}

const FEATURES = [
  { icon: UserSearch, title: <>Score <Hl>any customer</Hl></>, href: '#/score', cta: 'Score a customer',
    text: 'A calibrated probability that a customer leaves, the inputs that push it up or down, and whether they cross the flag threshold.',
    art: <MiniRows rows={[['Contract', 80, 'hot'], ['Tenure', 64, 'hot'], ['Internet service', 42, 'hot'], ['Tech support', 26], ['Payment method', 18]]} /> },
  { icon: FlaskConical, title: <>Ask <Hl>what if</Hl></>, href: '#/what-if', cta: 'Open the what-if lab',
    text: 'Edit the levers the business controls and watch the score move. Every result is cross-checked against a simpler model and labelled as model sensitivity, not a causal effect.',
    art: <MiniRows rows={[['Now', 72, 'hot'], ['One-year contract', 41], ['Tech support', 58], ['10% discount', 66]]} /> },
  { icon: ShieldCheck, title: <>Offer safely, <Hl>with approval</Hl></>, href: '#/queue', cta: 'Open the review queue',
    text: 'Guardrails run in code. Unknown account state or an unset policy limit always means a named person approves. Messages are checked for pressure language and invented offers.',
    art: <MiniRows rows={[['Vulnerable flag', 100], ['Escalation', 100], ['Contact cap', 70], ['Discount cap', 55]]} /> },
]

export default function Home() {
  const { meta, health, user } = useApp()
  const ev = meta.evaluation
  const tm = ev.test_metrics || {}, ci = ev.test_metrics_ci95 || {}
  const ciText = k => (ci[k] ? `95% CI ${num(ci[k][0])} to ${num(ci[k][1])}` : null)
  return (
    <>
      <section className="hero home-hero">
        <div className="wrap">
          <div>
            <div className="eyebrow">Churn risk · what-if · guarded offers</div>
            <h1>Keep more customers with <Hl>Evergreen</Hl></h1>
            <p className="lead">Find customers likely to leave, see why, test what the model reacts to, and route one safe, approved offer per customer to your CRM.</p>
            <div className="row">
              <a className="btn btn-primary btn-lg" href="#/score">Score a customer <ArrowRight size={18} /></a>
              <a className="btn btn-outline-light btn-lg" href="#/batch">Upload a file</a>
            </div>
          </div>
          <HeroArt auc={user.role === 'analyst' ? num(tm.roc_auc) : null} />
        </div>
      </section>

      {user.role === 'analyst' && (
        <div className="wrap lift">
          <div className="card">
            <div className="grid g4">
              <Stat k="Test ROC-AUC" v={num(tm.roc_auc)} s={ciText('roc_auc')} />
              <Stat k="Recall" v={pct(tm.recall)} s={ciText('recall')} />
              <Stat k="Precision" v={pct(tm.precision)} s={ciText('precision')} />
              <Stat k="Brier score" v={num(tm.brier)} s={ciText('brier')} />
            </div>
            <hr className="divider" />
            <div className="row spread small">
              <span className="row">
                <Badge tone="navy">{health.model}</Badge>
                <Badge>{health.calibration_method} calibration</Badge>
                <Badge tone={health.versions_match ? 'green' : 'amber'}>{health.versions_match ? 'Library versions match' : 'Library versions differ'}</Badge>
                <Badge tone={meta.threshold_is_placeholder ? 'yellow' : 'green'}>Threshold {num(meta.threshold)}{meta.threshold_is_placeholder ? ' · placeholder' : ' · cost-based'}</Badge>
              </span>
              <a href="#/model">Full evaluation and fairness <ArrowRight size={14} style={{ verticalAlign: -2 }} /></a>
            </div>
          </div>
        </div>
      )}

      <section className="section" style={{ paddingTop: 24 }}>
        <div className="wrap">
          <div className="section-head">
            <h2>From score to <Hl>approved message</Hl></h2>
            <p>One decision per customer: recommend, recommend pending approval, no action, or escalate to a human.</p>
          </div>
          {FEATURES.map((f, i) => (
            <div key={f.href} className={`feature ${i % 2 ? 'flip' : ''}`}>
              <div>
                <Badge><f.icon size={14} /> Step {i + 1}</Badge>
                <h2 style={{ marginTop: 14 }}>{f.title}</h2>
                <p>{f.text}</p>
                <a className="btn btn-primary" href={f.href}>{f.cta} <ArrowRight size={16} /></a>
              </div>
              <div className="feature-art">{f.art}</div>
            </div>
          ))}
        </div>
      </section>

      <section className="hero section">
        <div className="wrap">
          <div className="section-head">
            <h2 style={{ color: '#fff' }}>What this app will <Hl>and will not</Hl> claim</h2>
            <p style={{ color: 'rgba(255,255,255,.75)' }}>Every number comes from the saved model or the config files. Where a number is missing, the app says so instead of guessing.</p>
          </div>
          <div className="claims">
            {[
              ['Digital twin', 'Model sensitivity, not a causal estimate. Contract changes mostly reflect selection, and say so.'],
              ['Uplift', 'Trained on SIMULATED treatment: the data has no treatment column. Advisory only.'],
              ['Threshold', meta.threshold_is_placeholder ? 'A labelled placeholder until offer cost, customer value and success rate are set.' : 'Cost-based: offer_cost / (success_rate x customer_value).'],
              ['Guardrails', 'Plain code. Unknown account state or an unset limit always means human approval.'],
              ['Messages', 'The language model only phrases an offer already chosen. Code validates every draft.'],
              ['Business value', 'None is claimed. Expected net value stays empty until the business numbers exist.'],
            ].map(([h, p]) => (
              <div key={h} className="claim"><h4><CheckCircle2 size={15} style={{ verticalAlign: -2, marginRight: 6 }} />{h}</h4><p>{p}</p></div>
            ))}
          </div>
          <div className="row" style={{ justifyContent: 'center', marginTop: 36 }}>
            <a className="btn btn-yellow btn-lg" href="#/batch"><FileSpreadsheet size={18} /> Score a customer file</a>
          </div>
        </div>
      </section>
    </>
  )
}
