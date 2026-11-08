import io

import pandas as pd
import plotly.express as px
import streamlit as st

from src import data as D
from src import drift as DR
from src import explain as E
from src import messages as MSG
from src import recommender as R
from src import serving as S
from src import twin as TW
from src import ui

b = ui.setup('Batch + Drift')
batch, drift = st.tabs(['Batch scoring', 'Drift check'])
STATE_COLS = ['vulnerable', 'escalated', 'contacts_this_month', 'discount_pct_used_this_quarter']


def read_upload(f, sample_key: str, sample_label: str) -> pd.DataFrame | None:
    if f is not None:
        try:
            return pd.read_csv(f)
        except Exception as e:
            st.error('Could not read the CSV: %s' % e)
            return None
    if st.checkbox(sample_label, key=sample_key):
        return D.load_clean().drop(columns=['y'])
    return None


def tri(v):
    if pd.isna(v):
        return None
    return str(v).strip().lower() in ('1', 'true', 'yes', 'y') if not isinstance(v, bool) else v


with batch:
    ui.threshold_banner(b)
    st.markdown('Upload a CSV with the 17 model columns (Telco layout). Optional columns: `customerID`, `first_name`, and account state '
                '(`vulnerable`, `escalated`, `contacts_this_month`, `discount_pct_used_this_quarter`). **Anything not supplied is unknown, and unknown needs approval.**')
    raw = read_upload(st.file_uploader('Customer CSV', type='csv', key='b_file'), 'b_sample', 'Use the first 150 sample customers')
    if raw is not None:
        raw = raw.head(150) if st.session_state.get('b_sample') and st.session_state.get('b_file') is None else raw
        ref_col = 'customerID' if 'customerID' in raw.columns else None
        raw = raw.reset_index(drop=True)
        refs = raw[ref_col].astype(str) if ref_col else pd.Series(['row%d' % i for i in range(len(raw))])
        miss = [c for c in D.INPUTS if c not in raw.columns]
        if miss:
            st.error('Missing required columns: %s' % miss)
            st.stop()
        good, bad = [], []
        for i in range(len(raw)):
            row = S.customer_row(raw.loc[i])
            try:
                TW.CustomerTwin(row, b['meta'])
                good.append(i)
            except (TW.TwinError, S.InputError, KeyError, ValueError) as e:
                bad.append({'row': refs[i], 'reason': str(e)})
        scored = S.predict(raw.loc[good, D.INPUTS], b) if good else pd.DataFrame()
        flagged = scored.index[scored['flagged']] if len(scored) else []
        m = st.columns(4)
        m[0].metric('Rows', len(raw))
        m[1].metric('Scored', len(good))
        m[2].metric('Rejected', len(bad))
        m[3].metric('Flagged', len(flagged))
        if bad:
            with st.expander('Rejected rows (%d), with reasons' % len(bad), expanded=True):
                st.dataframe(pd.DataFrame(bad), hide_index=True)
        limit = st.number_input('Max flagged customers to run through the recommender', 1, 2000, 200)
        if len(good) and st.button('Run recommendations', type='primary'):
            out, bar = [], st.progress(0.0)
            todo = list(flagged)[: int(limit)]
            for j, i in enumerate(todo):
                st_ = {k: (tri(raw.loc[i, k]) if k in ('vulnerable', 'escalated') else (None if pd.isna(raw.loc[i, k]) else float(raw.loc[i, k]))) for k in STATE_COLS if k in raw.columns}
                has_state = bool(st_)
                rec = R.recommend(S.customer_row(raw.loc[i]), b, st_ if has_state else None, customer_ref=refs[i])
                first = str(raw.loc[i, 'first_name']).strip() if 'first_name' in raw.columns and not pd.isna(raw.loc[i, 'first_name']) else None
                msg = ''
                if rec['action'] and first:
                    try:
                        msg = MSG.draft(first, MSG.offer_from_changes(rec['action']['name'], rec['action']['changes']), rec['shap_reasons'], b['policy'])['message']
                    except ValueError:
                        msg = ''
                out.append({'customer_ref': refs[i], 'probability': rec['risk']['probability'], 'decision': rec['decision'], 'offer': (rec['action'] or {}).get('name', ''),
                            'changes': str((rec['action'] or {}).get('changes', '')), 'guardrails': '; '.join(ui.RULE_TEXT.get(f, f) for f in rec['fired_by']),
                            'top_reasons': E.sentence(rec['shap_reasons'] or []), 'first_name': first or '', 'message': msg})
                bar.progress((j + 1) / len(todo))
            res = pd.DataFrame(out).sort_values('probability', ascending=False)
            st.session_state['b_result'] = res
        res = st.session_state.get('b_result')
        if res is not None:
            st.subheader('Ranked recommendations')
            st.dataframe(res, hide_index=True, width='stretch')
            counts = res['decision'].value_counts()
            st.plotly_chart(px.bar(x=counts.index, y=counts.values, labels={'x': 'decision', 'y': 'customers'}), width='stretch')
            st.download_button('Download outbox draft (CSV)', res.to_csv(index=False).encode('utf-8'), file_name='outbox_draft.csv', mime='text/csv')
            st.caption('A draft, not a send list. Rows with decision "recommend_pending_approval" still need a named approver in the Review Queue. Messages are only drafted when the file has a first_name column. Twin ranking is model sensitivity, not causal.')

with drift:
    st.markdown('PSI of **tenure, MonthlyCharges and Contract** against the training data. < 0.10 stable, 0.10-0.25 moderate, > 0.25 major (a rule-of-thumb convention). Drift says the inputs moved, not that the model is wrong.')
    new = read_upload(st.file_uploader('"New month" CSV', type='csv', key='d_file'), 'd_sample', 'Use the held-out test customers (should look stable)')
    ref = DR.build_reference(b['meta']['train_reference'])
    if new is not None:
        try:
            res = DR.check(new, ref)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        c = st.columns(3)
        for col, r in zip(c, res.itertuples()):
            col.metric('PSI: %s' % r.feature, '%.3f' % r.psi, delta=r.status, delta_color='off' if r.status == 'stable' else 'inverse')
        st.dataframe(res.style.format({'psi': '{:.3f}'}), hide_index=True)
        if (res.status == 'MAJOR shift').any():
            st.error('Major shift in: %s. Review before trusting scores on this file.' % ', '.join(res[res.status == 'MAJOR shift'].feature))
        tr = pd.DataFrame(b['meta']['train_reference'])
        f1, f2 = st.columns(2)
        for col, f in zip((f1, f2), ('tenure', 'MonthlyCharges')):
            d = pd.concat([pd.DataFrame({f: tr[f], 'set': 'training'}), pd.DataFrame({f: new[f], 'set': 'new file'})])
            col.plotly_chart(px.histogram(d, x=f, color='set', barmode='overlay', histnorm='probability density', opacity=0.6, nbins=30), width='stretch')
        cc = pd.concat([tr['Contract'].value_counts(normalize=True).rename('training'), new['Contract'].value_counts(normalize=True).rename('new file')], axis=1).fillna(0)
        st.plotly_chart(px.bar(cc, barmode='group', labels={'value': 'share', 'index': 'Contract'}), width='stretch')
