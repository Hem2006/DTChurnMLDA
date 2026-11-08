"""Write the experiment results into a copy of the paper's .docx (string-level edits of word/document.xml; layout untouched).

    .venv/Scripts/python -m experiments.fill_paper <in.docx> <out.docx>

Every replacement must match exactly once, otherwise the script stops without writing anything.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
R = json.loads((ROOT / 'experiments/results/model_experiments.json').read_text())
G = json.loads((ROOT / 'experiments/results/guardrail_ablation.json').read_text())


def f3(x): return '%.3f' % x
def f4(x): return '%.4f' % x
def ci(c, f=f3): return '%s – %s' % (f(c[0]), f(c[1]))


def once(x: str, old: str, new: str) -> str:
    n = x.count(old)
    if n != 1:
        raise SystemExit('expected exactly one match, found %d: %r' % (n, old[:90]))
    return x.replace(old, new)


def text(x, old, new):
    return once(x, escape(old), escape(new))


def para_tail(x, start, new):
    """Replace everything from `start` to the end of its paragraph (later runs included) with `new`, in start's run formatting."""
    s = escape(start)
    if x.count(s) != 1:
        raise SystemExit('para_tail: %d matches for %r' % (x.count(s), start[:80]))
    i = x.index(s); t_end = x.index('</w:t>', i); p_end = x.index('</w:p>', i)
    rest = x[x.index('</w:r>', t_end) + len('</w:r>'):p_end]
    rest = re.sub(r'<w:r>.*?</w:r>', '', rest, flags=re.S)
    return x[:i] + escape(new) + x[t_end:x.index('</w:r>', t_end) + len('</w:r>')] + rest + x[p_end:]


def table_bounds(x, anchor):
    i = x.index(escape(anchor)); s = x.rindex('<w:tbl>', 0, i); e = x.index('</w:tbl>', i) + len('</w:tbl>')
    return s, e


def cell_in_row(x, row_label, col_index, new, anchor):
    """Replace the text of cell col_index (0-based) in the row whose first cell is row_label, inside the table containing anchor."""
    s, e = table_bounds(x, anchor)
    t = x[s:e]
    rows = re.findall(r'<w:tr>.*?</w:tr>', t, flags=re.S)
    hit = [r for r in rows if re.search(r'<w:t[^>]*>%s</w:t>' % re.escape(escape(row_label)), r.split('</w:tc>')[0])]
    if len(hit) != 1:
        raise SystemExit('row %r: %d matches' % (row_label, len(hit)))
    r = hit[0]
    cells = re.findall(r'<w:tc>.*?</w:tc>', r, flags=re.S)
    c = cells[col_index]
    ts = re.findall(r'<w:t(?: [^>]*)?>[^<]*</w:t>', c)
    c2 = c.replace(ts[0], '<w:t xml:space="preserve">%s</w:t>' % escape(new), 1)
    for extra in ts[1:]:
        c2 = c2.replace(extra, '<w:t></w:t>', 1)
    r2 = r.replace(c, c2, 1)
    return x[:s] + t.replace(r, r2, 1) + x[e:]


def set_row_texts(row_xml, values):
    cells = re.findall(r'<w:tc>.*?</w:tc>', row_xml, flags=re.S)
    out = row_xml
    for c, v in zip(cells, values):
        t = re.findall(r'<w:t(?: [^>]*)?>[^<]*</w:t>', c)[0]
        out = out.replace(c, c.replace(t, '<w:t xml:space="preserve">%s</w:t>' % escape(v), 1), 1)
    return out


def main(src, dst):
    z = zipfile.ZipFile(src)
    x = z.read('word/document.xml').decode('utf8')
    rk = R['ranking']; cal = R['calibration_ablation']; dl = R['delong_vs_served']; ab = R['feature_ablation']
    ev = R['uplift_simulation_evaluation']; uc = R['uplift_simulation_config']; rp = uc['retention_probability_by_contract']
    ps = R['profit_grid']['summary']; n = ps['cells']
    ex = next(r for r in R['profit_grid']['rows'] if (r['c'], r['v'], r['s']) == (20, 800, 0.3))

    # ---- Section III: simulated uplift configuration
    x = text(x, '[TO BE RUN: report the configuration used to simulate the treatment effect model]',
             '(config.yaml, seed %d: treatment is assigned at random with probability %.1f, independently of the customer; a treated customer '
             'who would have churned is retained with probability %.2f on a month-to-month contract, %.2f on a one-year contract and %.2f on a '
             'two-year contract, so the assumed uplift of a customer is P(churn | x) times that probability). The T-learner is fitted on the '
             'simulated training split (%s treated, %s control); on the simulated test split its estimates correlate %.2f with the uplift the '
             'simulation assumed (mean %.3f vs %.3f), which checks the code path, not any real customer response'
             % (uc['seed'], uc['treatment_probability'], rp['Month-to-month'], rp['One year'], rp['Two year'], format(ev['n_treated'], ','), format(ev['n_control'], ','),
                ev['corr_estimated_vs_assumed_uplift_test'], ev['mean_estimated_uplift_test'], ev['mean_assumed_uplift_test']))

    # ---- Section IV: setup facts
    x = text(x, '[TO BE RUN: state inner loop folds, search spaces and number of iterations]',
             '(3-fold stratified CV inside each of the 5 outer folds, scored by ROC AUC; logistic regression: grid of 13 values of C from 10⁻³ to 10 '
             'with balanced class weights; random forest: 8 random-search iterations over trees, depth, leaf size and features per split; '
             'XGBoost: 25 iterations over trees, learning rate, depth, row and column subsampling, min child weight, gamma, L2 penalty and '
             'positive-class weight; LightGBM: 15 iterations over a similar space with leaf count instead of depth)')
    x = text(x, '[TO BE RUN: state the binning used for ECE]',
             '(10 equal-frequency bins; the absolute gap between mean score and churn rate in each bin, weighted by the share of customers in the bin). '
             'The ECE depends on the binning: for the served model it is %s with 10 equal-frequency bins, %s with 10 equal-width bins and %s with 15 '
             'equal-frequency bins' % (f3(cal['isotonic']['test_ece_quantile10']), f3(cal['isotonic']['test_ece_uniform10']),
                                       f3(cal['isotonic']['test_ece_quantile15'])))
    x = text(x, '[TO BE RUN: how the 40 customers were selected, whether they provided account state or not]',
             '(the first 40 rows of the CSV in file order, read through the default column mapping, which supplies no first names and no account '
             'state, so every account state was unknown; re-running it reproduced the same counts)')

    # ---- Table III
    a = rk['xgboost_served_calibrated']
    x = cell_in_row(x, 'PR-AUC', 1, f3(a['pr_auc']), 'Lift (top decile)')
    x = cell_in_row(x, 'PR-AUC', 2, ci(a['pr_auc_ci95']), 'Lift (top decile)')
    x = cell_in_row(x, 'Lift (top decile)', 1, '%.2f' % a['lift_top_decile'], 'Lift (top decile)')
    x = cell_in_row(x, 'Lift (top decile)', 2, ci(a['lift_top_decile_ci95'], lambda v: '%.2f' % v), 'Lift (top decile)')
    x = text(x, 'These operating-point figures are based on the placeholder threshold and may vary if actual costs are determined.',
             'These operating-point figures are based on the placeholder threshold and may vary if actual costs are determined. The threshold-free '
             'ranking measures are PR-AUC %s (95%% CI %s; base rate %s) and a top-decile lift of %.2f: the 10%% of customers with the highest score churn '
             'at %.2f times the overall rate.' % (f3(a['pr_auc']), ci(a['pr_auc_ci95']), f3(R['sanity']['base_rate_test']), a['lift_top_decile'],
                                                  a['lift_top_decile']))
    x = x.replace(escape('the precision is 0.542 and the recall is 0.757, meaning that about one flagged customer is out of two churners'),
                  escape('the precision is 0.542 and the recall is 0.757, meaning that about one flagged customer in two is a churner and about three in four churners are flagged'), 1)

    # ---- Table IV
    T4 = 'Contract + tenure baseline'
    for label, key in ((T4, 'baseline_contract_tenure'), ('Logistic regression', 'logreg_reference'), ('Random forest', 'random_forest'),
                       ('XGBoost (served, calibrated)', 'xgboost_served_calibrated'), ('LightGBM', 'lightgbm')):
        x = cell_in_row(x, label, 3, f3(rk[key]['pr_auc']), T4)
    x = cell_in_row(x, 'Random forest', 2, f3(rk['random_forest']['roc_auc']) + '†', T4)
    x = cell_in_row(x, 'LightGBM', 2, f3(rk['lightgbm']['roc_auc']) + '†', T4)
    x = text(x, '* Logistic-regression reference with balanced class weights, not calibrated; AUC is unaffected by calibration.',
             '* Logistic-regression reference with balanced class weights, not calibrated; AUC is unaffected by calibration. † Best model of the same '
             '5-fold randomised search as the served XGBoost, refit on all of train, not calibrated; scored once on the test set for this table only.')
    d = dl['logreg_reference']
    x = text(x, 'Logistic regression is thus within noise of XGBoost, and we do not claim that the XGBoost model is better.',
             'In nested CV, logistic regression is thus within noise of XGBoost.')
    x = para_tail(x, 'A paired DeLong test [36] of the served model and logistic regression',
             'A paired DeLong test [36] on the test set is more sensitive, because both models are scored on the same customers: it gives an AUC '
             'difference of %s in favour of the served model (95%% CI %s, z = %.2f, p = %.3f). On this split, then, the gap to the logistic-regression '
             'reference is small but not noise. Against random forest (difference %s, p = %.3f) and LightGBM (%s, p = %.2f) no difference is '
             'detected. Test PR-AUC orders the models in the same way (Table IV). We still do not claim the tree model is materially better: a gap '
             'of 0.01 AUC on one public split is not a deployment argument.'
             % (f4(d['diff']), ci(d['ci95_diff'], f4), d['z'], d['p_value'], f4(dl['random_forest']['diff']), dl['random_forest']['p_value'],
                f4(dl['lightgbm']['diff']), dl['lightgbm']['p_value']))

    # ---- Table V + calibration text
    c_n, c_i, c_s = cal['none'], cal['isotonic'], cal['sigmoid']
    x = cell_in_row(x, 'Platt scaling (Brier / ECE)', 2, '%s / %s' % (f4(c_s['test_brier']), f3(c_s['test_ece_quantile10'])), 'Quantity (test set)')
    s, e = table_bounds(x, 'Quantity (test set)')
    t = x[s:e]
    last = re.findall(r'<w:tr>.*?</w:tr>', t, flags=re.S)[-1]
    new_row = set_row_texts(last, ['F1-optimal threshold (OOF, train)', f3(c_n['f1_optimal_threshold_oof']), f3(c_i['f1_optimal_threshold_oof'])])
    x = x[:s] + t.replace(last, last + new_row, 1) + x[e:]
    x = text(x, 'A comparison with Platt scaling and without calibration is not yet available.',
             'Platt (sigmoid) scaling, fitted the same way (5-fold ensemble on train), gives a test Brier score of %s and an ECE of %s: better than no '
             'calibration, slightly worse than isotonic, which matches the out-of-fold Brier ranking on train (%s isotonic, %s Platt, %s none) that '
             'chose isotonic. The F1-optimal placeholder threshold depends on the calibration: %s uncalibrated, %s isotonic, %s Platt. AUC barely moves '
             '(%s, %s, %s), as expected for monotone maps.'
             % (f4(c_s['test_brier']), f3(c_s['test_ece_quantile10']), f4(c_i['oof_brier_train']), f4(c_s['oof_brier_train']), f4(c_n['oof_brier_train']),
                f3(c_n['f1_optimal_threshold_oof']), f3(c_i['f1_optimal_threshold_oof']), f3(c_s['f1_optimal_threshold_oof']),
                f3(c_n['test_roc_auc']), f3(c_i['test_roc_auc']), f3(c_s['test_roc_auc'])))

    # ---- Table VIII: add a test column and variants E-G
    s, e = table_bounds(x, 'Feature-set variant')
    t = x[s:e]
    t = t.replace('<w:gridCol w:w="3200"/><w:gridCol w:w="1600"/>', '<w:gridCol w:w="2700"/><w:gridCol w:w="1050"/><w:gridCol w:w="1050"/>')
    t = t.replace('w:w="3200"', 'w:w="2700"').replace('<w:tcW w:type="dxa" w:w="1600"/>', '<w:tcW w:type="dxa" w:w="1050"/>')
    rows = re.findall(r'<w:tr>.*?</w:tr>', t, flags=re.S)
    keys = {'A: TotalCharges blanks = median': 'A: TotalCharges blanks = median', 'B: TotalCharges blanks = 0': 'B: TotalCharges blanks = 0',
            'C: drop TotalCharges': 'C: drop TotalCharges', 'D: drop TotalCharges + gender (served)': 'D: drop TotalCharges + gender (served)'}
    for r in rows:
        cells = re.findall(r'<w:tc>.*?</w:tc>', r, flags=re.S)
        label = re.sub('<[^>]+>', '', cells[0])
        last = cells[-1]
        tt = re.findall(r'<w:t(?: [^>]*)?>[^<]*</w:t>', last)[0]
        val = 'Mean test AUC' if label == 'Feature-set variant' else f4(ab[keys[label]]['test_mean_auc'])
        t = t.replace(r, r.replace(last, last + last.replace(tt, '<w:t>%s</w:t>' % escape(val), 1), 1), 1)
    rows = re.findall(r'<w:tr>.*?</w:tr>', t, flags=re.S)
    extra = ''
    for k, lab in (('E: drop gender only (TotalCharges median)', 'E: drop gender only'),
                   ('F: D + drop SeniorCitizen, Partner, Dependents', 'F: D + drop SeniorCitizen, Partner, Dependents'),
                   ('G: D + drop MonthlyCharges', 'G: D + drop MonthlyCharges')):
        extra += set_row_texts(rows[-1], [lab, f4(ab[k]['cv_mean_auc']), f4(ab[k]['test_mean_auc'])])
    t = t.replace(rows[-1], rows[-1] + extra, 1)
    x = x[:s] + t + x[e:]
    x = text(x, 'TABLE VIII. FEATURE-SET ABLATION (MEAN CV AUC, TRAINING SET)', 'TABLE VIII. FEATURE-SET ABLATION (MEAN CV AUC ON TRAIN; MEAN TEST AUC)')
    tm = [ab[k]['test_mean_auc'] for k in ab]
    x = text(x, '[TO BE RUN: confirm the cross validation protocol used for this ablation]',
             '(protocol: fixed, untuned logistic regression and XGBoost, 5-fold stratified CV repeated twice on the training set; each figure is the mean '
             'of the two models\' AUCs). Refitting the same two models on all of train and scoring the test set once gives the same picture: every '
             'variant lies between %s and %s mean test AUC. Three further variants change CV AUC by less than 0.001: dropping gender only (E), '
             'also dropping the three audited demographic inputs SeniorCitizen, Partner and Dependents (F, test %s vs %s for the served set), and '
             'also dropping MonthlyCharges (G). Variant F matters for the subgroup audit: the demographic inputs add almost nothing to discrimination'
             % (f4(min(tm)), f4(max(tm)), f4(ab['F: D + drop SeniorCitizen, Partner, Dependents']['test_mean_auc']),
                f4(ab['D: drop TotalCharges + gender (served)']['test_mean_auc'])))

    # ---- Pipeline behaviour: guardrail ablation; message safety not run
    gc = G['configs']
    def dec(k, d): return gc[k]['decisions'].get(d, 0)
    off, unk, sim_s, hyp_c, hyp_s = gc['off'], gc['shipped_state_unknown'], gc['shipped_state_simulated'], gc['hypothetical_limits_state_clean'], gc['hypothetical_limits_state_simulated']
    tch = hyp_s['customers_touched_by_rule']
    x = para_tail(x, 'The number of actions each guardrail blocks or routes to approval',
             'Across the %d flagged test customers, turning the guardrails off would auto-recommend an action for %d of them. With guardrails.yaml as '
             'shipped, %d go to pending approval whether or not account state is supplied: the unset contact cap touches all %d and the unset discount '
             'cap the %d whose best action carries a discount. With SIMULATED account states (labelled shares: 5%% vulnerable, 3%% escalated, 10%% not '
             'supplied), %d are escalated and %d wait for approval. Only when HYPOTHETICAL limits are also set (2 contacts a month, 15%% discount a quarter) '
             'do customers get auto-recommendations: %d of %d with a clean state, and %d with the simulated states, where %d are escalated (vulnerable %d, '
             'escalated %d), the contact cap blocks some action for %d, the discount cap for %d, and %d with unknown state still need approval. A replay '
             'check against the live recommender on %d sampled customers found %d mismatches. The rate at which model drafts fail validation and fall '
             'back to the template was not measured: it needs live language-model calls, and none were made for this paper.'
             % (G['n_flagged'], dec('off', 'recommend'), dec('shipped_state_unknown', 'recommend_pending_approval'), unk['customers_touched_by_rule'].get('contact_cap_unset', 0),
                unk['customers_touched_by_rule'].get('discount_cap_unset', 0), dec('shipped_state_simulated', 'escalate_to_human'),
                dec('shipped_state_simulated', 'recommend_pending_approval'), dec('hypothetical_limits_state_clean', 'recommend'), G['n_flagged'],
                dec('hypothetical_limits_state_simulated', 'recommend'), dec('hypothetical_limits_state_simulated', 'escalate_to_human'),
                tch.get('vulnerable_account', 0), tch.get('escalated_account', 0), tch.get('contact_cap_exceeded', 0), tch.get('discount_cap_exceeded', 0),
                tch.get('unknown_state', 0), G['replay_check']['sampled_customers'], G['replay_check']['mismatches']))

    # ---- Profit grid + Table IX + pending wording
    x = text(x, 'Pending Experiments', 'Follow-up Experiments')
    x = text(x, 'The following experiments are still to be listed in Table IX.', 'Table IX lists the follow-up experiments and where their results are reported.')
    x = para_tail(x, 'The profit analysis will compare three strategies',
             'The profit analysis compares three strategies (no action, flag everyone, and the threshold in (1)) by the profit y · s · v − c per contacted '
             'customer over a labelled grid of hypothetical values of c, v and s; it is a sensitivity analysis, not a business result. The grid is c ∈ {5, 10, 20, 50}, v ∈ {200, 500, 800, 1500} and s ∈ {0.1, 0.2, 0.3, 0.5} (%d cells, all hypothetical); profit is computed '
             'on the 1,409 test customers from their observed churn, assuming the offer only affects would-be churners. The cost threshold t* gives '
             'the highest or tied-highest profit in %d of %d cells, beats the 0.315 placeholder in %d, and never does worse than no action. Where it '
             'loses, the margin is small: flagging everyone wins narrowly when t* is very low, and the placeholder wins narrowly in four cells where t* is close to it (0.25 to 0.31). For '
             'c = 20, v = 800, s = 0.3 (t* = 0.083) it yields %.1f per customer against %.1f for flagging everyone and %.1f for the placeholder.'
             % (n, round(ps['cost_threshold_best_or_tied_share'] * n), n, round(ps['cost_threshold_beats_placeholder_share'] * n),
                ex['cost_threshold']['realised_profit_per_customer'], ex['flag_everyone']['realised_profit_per_customer'],
                ex['placeholder_0.315']['realised_profit_per_customer']))
    x = text(x, 'TABLE IX. EXPERIMENTS STILL TO BE RUN', 'TABLE IX. FOLLOW-UP EXPERIMENTS AND STATUS')
    status = [('Ranking quality', 'Done: Tables III, IV'), ('Paired test', 'Done: Section V-B'), ('Profit sensitivity', 'Done: this section'),
              ('Calibration ablation', 'Done: Table V'), ('Feature-set ablation', 'Done: Table VIII'), ('Guardrail ablation', 'Done: Section V-G'),
              ('Message safety', 'Not run: needs live LLM calls')]
    for lab, st in status:
        x = cell_in_row(x, lab, 2, st, 'What will be reported')
    x = text(x, 'What will be reported', 'What is reported')
    x = text(x, 'as well as a list of experiments still to be conducted', 'as well as the follow-up experiments listed in Table IX')
    x = text(x, 'The experiments listed in Table IX are to be completed first, pending.',
             'Of the experiments in Table IX, only the message-safety measurement remains; it needs live language-model calls.')
    left = x.count('TO BE RUN')
    if left:
        raise SystemExit('%d TO BE RUN markers left' % left)

    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as out:
        for item in z.infolist():
            out.writestr(item, x.encode('utf8') if item.filename == 'word/document.xml' else z.read(item.filename))
    print('wrote', dst)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
