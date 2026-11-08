import { describe, expect, it } from 'vitest'
import { DEFAULT_CUSTOMER, accountStateFromRow, changesText, chunk, fairnessGaps, makeCoherent, missingColumns, toCsv } from './lib.js'
import { formatDetail } from '../api.js'

describe('makeCoherent (mirrors app.dash_logic.make_coherent)', () => {
  it('forces add-ons when there is no internet, and resets them when internet returns', () => {
    const c = makeCoherent({ ...DEFAULT_CUSTOMER, InternetService: 'No', TechSupport: 'Yes' })
    expect(c.TechSupport).toBe('No internet service')
    expect(c.StreamingTV).toBe('No internet service')
    expect(makeCoherent({ ...c, InternetService: 'DSL' }).TechSupport).toBe('No')
  })
  it('handles phone lines the same way', () => {
    const c = makeCoherent({ ...DEFAULT_CUSTOMER, PhoneService: 'No', MultipleLines: 'Yes' })
    expect(c.MultipleLines).toBe('No phone service')
    expect(makeCoherent({ ...c, PhoneService: 'Yes' }).MultipleLines).toBe('No')
  })
})

describe('account state from a file row', () => {
  it('treats blank or unrecognised values as unknown, never as safe', () => {
    expect(accountStateFromRow({})).toEqual({ vulnerable: null, escalated: null, contacts_this_month: null, discount_pct_used_this_quarter: null })
    expect(accountStateFromRow({ vulnerable: 'maybe', escalated: 'no', contacts_this_month: '-1', discount_pct_used_this_quarter: '150' }))
      .toEqual({ vulnerable: null, escalated: false, contacts_this_month: null, discount_pct_used_this_quarter: null })
    expect(accountStateFromRow({ vulnerable: 'Yes', escalated: '0', contacts_this_month: '2', discount_pct_used_this_quarter: '7.5' }))
      .toEqual({ vulnerable: true, escalated: false, contacts_this_month: 2, discount_pct_used_this_quarter: 7.5 })
  })
})

describe('fairnessGaps (same definition as app.fairness)', () => {
  const groups = [
    { attribute: 'Dependents', group: 'No', n: 900, recall: 0.8, precision: 0.5, flag_rate: 0.45 },
    { attribute: 'Dependents', group: 'Yes', n: 431, recall: 0.55, precision: 0.48, flag_rate: 0.2 },
  ]
  it('is max minus min, flagged strictly above the limit', () => {
    const g = fairnessGaps(groups, ['recall', 'precision'], 0.1)
    expect(g.find(x => x.metric === 'recall')).toMatchObject({ highest: 'No', lowest: 'Yes', flagged: true, min_n: 431 })
    expect(g.find(x => x.metric === 'recall').gap).toBeCloseTo(0.25)
    expect(g.find(x => x.metric === 'precision').flagged).toBe(false)
  })
})

describe('small helpers', () => {
  it('finds missing columns', () => expect(missingColumns(['tenure ', 'Contract'], ['tenure', 'Contract', 'MonthlyCharges'])).toEqual(['MonthlyCharges']))
  it('chunks', () => expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]))
  it('writes CSV with quoting', () => expect(toCsv([{ a: 'x,y', b: 'say "hi"' }], ['a', 'b'])).toBe('a,b\n"x,y","say ""hi"""'))
  it('describes changes', () => expect(changesText({ Contract: 'One year', discount_pct: 5 })).toBe('Contract: One year · 5% discount'))
  it('formats API errors', () => {
    expect(formatDetail([{ loc: ['body', 'customer', 'tenure'], msg: 'Input should be a valid integer' }])).toBe('customer.tenure: Input should be a valid integer')
    expect(formatDetail({ error: 'no message: nothing to offer', reasons: ['Below threshold.'] })).toBe('no message: nothing to offer. Below threshold.')
  })
})
