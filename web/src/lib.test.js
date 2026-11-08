import { describe, expect, it } from 'vitest'
import { pctRisk, changesText, cohortCurve, filterMask, leftBy, lostAt, money, pct, revenueCurve, riskColor, setEdit, signed, survival } from './lib.js'

describe('survival (ILLUSTRATIVE: p read as leaving within 12 months, constant hazard)', () => {
  it('is 1 at month 0 and 1-p at month 12', () => {
    expect(survival(0.4, 0)).toBe(1)
    expect(survival(0.4, 12)).toBeCloseTo(0.6, 10)
  })
  it('falls monotonically and clamps the month range', () => {
    const s = [0, 3, 6, 9, 12].map(m => survival(0.5, m))
    expect(s).toEqual([...s].sort((a, b) => b - a))
    expect(survival(0.5, 99)).toBeCloseTo(0.5, 10)
    expect(survival(0.5, -4)).toBe(1)
  })
  it('a lower risk never loses a customer a higher risk keeps (common random numbers)', () => {
    for (const u of [0.05, 0.2, 0.5, 0.9]) for (let m = 0; m <= 12; m++) {
      if (leftBy(u, 0.2, m)) expect(leftBy(u, 0.6, m)).toBe(true)
    }
  })
  it('by month 12 the expected share lost equals p', () => {
    const n = 20000, ps = new Array(n).fill(0.3), us = Array.from({ length: n }, (_, i) => (i + 0.5) / n), mc = new Array(n).fill(10)
    const r = lostAt(us, ps, mc, 12)
    expect(r.lost / n).toBeCloseTo(0.3, 2)
    expect(r.mrr).toBe(r.lost * 10)
    expect(lostAt(us, ps, mc, 0).lost).toBe(0)
  })
})

describe('curves', () => {
  it('cohort curve starts at n and ends at the sum of (1-p)', () => {
    const c = cohortCurve([0.1, 0.5, 0.9])
    expect(c[0]).toBe(3)
    expect(c[12]).toBeCloseTo(0.9 + 0.5 + 0.1, 10)
    expect(c).toHaveLength(13)
  })
  it('revenue curve is monthly charge times survival', () => {
    const r = revenueCurve(0.5, 80)
    expect(r[0]).toBe(80)
    expect(r[12]).toBeCloseTo(40, 10)
  })
})

describe('colour and formatting', () => {
  it('safe is greener than risky', () => {
    const a = riskColor(0), b = riskColor(0.9)
    expect(a[1]).toBeGreaterThan(a[0])
    expect(b[0]).toBeGreaterThan(b[1])
    expect(riskColor(5)).toEqual(riskColor(0.9 * 5))
    expect(riskColor(NaN)).toEqual(riskColor(0))
  })
  it('formats', () => {
    expect(pct(0.371)).toBe('37%')
    expect(pct(null)).toBe('–')
    expect(pctRisk(1)).toBe('>99%')
    expect(pctRisk(0)).toBe('<1%')
    expect(pctRisk(0.371)).toBe('37%')
    expect(pctRisk(0.371, 1)).toBe('37.1%')
    expect(money(1234.5)).toBe('$1,235')
    expect(signed(-3)).toBe('−3')
    expect(signed(3)).toBe('+3')
    expect(changesText({ Contract: 'One year', discount_pct: 10 })).toBe('Contract → One year · Discount 10%')
    expect(changesText({})).toBe('No change')
  })
})

describe('edits and filters', () => {
  const cust = { Contract: 'Month-to-month', TechSupport: 'No' }
  it('setEdit keeps only differences', () => {
    let e = setEdit({}, cust, 'Contract', 'One year')
    expect(e).toEqual({ Contract: 'One year' })
    e = setEdit(e, cust, 'Contract', 'Month-to-month')
    expect(e).toEqual({})
    expect(setEdit({}, cust, 'discount_pct', 0)).toEqual({})
    expect(setEdit({}, cust, 'discount_pct', 10)).toEqual({ discount_pct: 10 })
  })
  it('filterMask combines contract, internet and flagged', () => {
    const nodes = [
      { p: 0.7, c: { Contract: 'Month-to-month', InternetService: 'DSL' } },
      { p: 0.1, c: { Contract: 'Month-to-month', InternetService: 'DSL' } },
      { p: 0.7, c: { Contract: 'Two year', InternetService: 'No' } },
    ]
    const none = { contracts: new Set(), internet: new Set(), flaggedOnly: false }
    expect([...filterMask(nodes, none, 0.3)]).toEqual([1, 1, 1])
    expect([...filterMask(nodes, { ...none, contracts: new Set(['Month-to-month']) }, 0.3)]).toEqual([1, 1, 0])
    expect([...filterMask(nodes, { ...none, flaggedOnly: true }, 0.3)]).toEqual([1, 0, 1])
    expect([...filterMask(nodes, { contracts: new Set(['Two year']), internet: new Set(['No']), flaggedOnly: true }, 0.3)]).toEqual([0, 0, 1])
  })
})

describe('retention app helpers (plain language)', () => {
  it('labels options and case statuses without model jargon', async () => {
    const { optionLabel, caseStatusText, OPTION_LABELS } = await import('./lib.js')
    expect(optionLabel('price_cut_10')).toBe('10% off the monthly bill')
    expect(optionLabel('new_thing')).toBe('new thing')
    expect(optionLabel(null)).toBe('–')
    expect(Object.keys(OPTION_LABELS)).toEqual(['do_nothing', 'price_cut_10', 'contract_one_year', 'add_tech_support', 'combined_package'])
    expect(caseStatusText('pending_approval')).toEqual(['Waiting for approval', 'warn'])
    expect(caseStatusText(null)[0]).toBe('Not worked on this run')
    expect(caseStatusText('odd_state')).toEqual(['odd state', 'muted'])
  })
  it('turns guardrail approval reasons into plain sentences, once each', async () => {
    const { approvalReasons, approvalText } = await import('./lib.js')
    const raw = ['vulnerable flag not supplied: cannot verify the account may be contacted',
      'escalated flag not supplied: cannot verify the account may be contacted',
      'max_contacts_per_month is not configured in guardrails.yaml',
      'max_discount_pct_per_customer_per_quarter is not configured in guardrails.yaml',
      'max_contacts_per_month is not configured in guardrails.yaml']
    expect(approvalReasons(raw)).toHaveLength(4)
    expect(approvalText('something else')).toBe('something else')
    expect(approvalReasons(null)).toEqual([])
  })
  it('writes CSV with quoting', async () => {
    const { toCsv } = await import('./lib.js')
    expect(toCsv([{ a: 'x', b: 'he said "hi", ok' }, { a: null, b: 2 }], ['a', 'b'])).toBe('a,b\r\nx,"he said ""hi"", ok"\r\n,2')
  })
  it('formats ages and point changes', async () => {
    const { ago, pts } = await import('./lib.js')
    const now = Date.parse('2026-01-01T12:00:00Z')
    expect(ago('2026-01-01T11:59:50Z', now)).toBe('just now')
    expect(ago('2026-01-01T11:50:00Z', now)).toBe('10 min ago')
    expect(ago('2026-01-01T09:00:00Z', now)).toBe('3 h ago')
    expect(ago(null)).toBe('–')
    expect(pts(0.5, 0.377)).toBe('−12.3 pts')
    expect(pts(0.2, 0.25)).toBe('+5.0 pts')
    expect(pts(null, 0.2)).toBe('–')
  })
})
