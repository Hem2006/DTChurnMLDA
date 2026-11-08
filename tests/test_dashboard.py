import pytest
from streamlit.testing.v1 import AppTest

from app import dash_logic as dl
from conftest import ROOT

APP = str(ROOT / 'dashboard' / 'app.py')
PAGES = ['Overview', 'Build a customer', 'Score a file', 'Customer lookup', 'Digital-twin lab', 'Recommendation', 'Message', 'Review queue', 'Fairness']


@pytest.fixture(scope='module')
def bundle():
    return dl.load_bundle()


@pytest.fixture(scope='module')
def customers():
    return dl.load_customers()


# ---------------------------------------------------------------- logic
def test_overview_no_revenue_without_customer_value(bundle, customers):
    o = dl.overview(bundle, customers.iloc[:400])
    assert o['revenue_at_risk'] is None and 'customer_value' in o['revenue_at_risk_reason']
    assert o['n'] == 400 and 0 < o['share_flagged'] < 1 and o['threshold_is_placeholder']


def test_overview_revenue_only_with_config_number(bundle, customers):
    sub = customers.iloc[:400]
    b = dict(bundle, config={**bundle['config'], 'customer_value': 800})
    o = dl.overview(b, sub)
    p = o['probabilities']
    assert o['revenue_at_risk'] == pytest.approx(float((p[p >= o['threshold']] * 800).sum()))
    assert o['revenue_at_risk_reason'] is None


def test_customer_view_and_reasons_only_when_flagged(bundle, customers):
    ids = dl.test_split_ids()
    flagged = unflagged = None
    for cid in ids[:80]:
        v = dl.customer_view(bundle, dl.to_model_row(customers.loc[cid], bundle['meta']))
        if v['prediction']['flagged'] and flagged is None:
            flagged = v
        if not v['prediction']['flagged'] and unflagged is None:
            unflagged = v
    assert flagged['reasons'] and unflagged['reasons'] is None
    assert list(flagged['contributions'].index)[0] in bundle['meta']['input_columns']


def test_scenario_table_columns(bundle, customers):
    from app import twin as tw
    run = tw.run_scenarios(dl.to_model_row(customers.iloc[0], bundle['meta']), bundle)
    t = dl.scenario_table(run)
    assert {'scenario', 'risk drop (raw)', 'confidence', 'warning', 'interpretation'} <= set(t.columns)
    assert (t['interpretation'] == 'model sensitivity, not a causal estimate').all()


# ---------------------------------------------------------------- UI (headless)
@pytest.mark.parametrize('page', PAGES)
def test_every_page_renders_without_exception(page, tmp_path, monkeypatch):
    monkeypatch.setenv('CHURN_DB', str(tmp_path / 'dash.sqlite'))
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception, at.exception
    at.sidebar.radio[0].set_value(page).run()
    assert not at.exception, (page, [e.value for e in at.exception])
    assert at.title[0].value


def test_placeholder_banner_and_labels():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert any('PLACEHOLDER' in w.value for w in at.sidebar.warning)
    assert any('SIMULATED' in c.value for c in at.sidebar.caption)
    at.sidebar.radio[0].set_value('Digital-twin lab').run()
    assert any('NOT A CAUSAL ESTIMATE' in e.value for e in at.error)
    assert any('selection' in w.value for w in at.warning) or any('LOW CONFIDENCE' in w.value for w in at.warning) or len(at.warning) > 0


def test_overview_shows_revenue_na():
    at = AppTest.from_file(APP, default_timeout=120).run()
    assert any(m.label == 'Revenue at risk' and m.value == 'n/a' for m in at.metric)
    assert any('customer_value' in i.value for i in at.info)


def test_recommendation_page_labels_simulated_uplift():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value('Recommendation').run()
    assert not at.exception
    text = ' '.join(w.value for w in at.warning) + ' '.join(i.value for i in at.info) + ' '.join(m.value for m in at.markdown)
    assert 'SIMULATED' in text + ' '.join(c.value for c in at.caption)


def test_message_page_rejects_bad_edit_and_needs_name():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value('Message').run()
    assert not at.exception
    assert any('first name' in i.value for i in at.info) or any('No message' in i.value for i in at.info)


def test_fairness_page_shows_flagged_gaps():
    at = AppTest.from_file(APP, default_timeout=180).run()
    at.sidebar.radio[0].set_value('Fairness').run()
    assert not at.exception
    assert any('Dependents recall gap' in w.value for w in at.warning)


def test_flagged_only_checkbox_gives_a_flagged_customer_and_an_offer():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value('Recommendation').run()
    box = next(c for c in at.checkbox if c.key == 'rc_flagged')
    box.check().run()
    assert not at.exception
    assert any('recommend pending approval' in s.value for s in at.warning)        # account state left unknown -> needs approval


def test_review_queue_page_approves_a_pending_case(tmp_path, monkeypatch):
    from app import pipeline as pl
    from test_twin import FIBER
    db = tmp_path / 'dash.sqlite'
    monkeypatch.setenv('CHURN_DB', str(db))
    b = pl.load_bundle()
    pl.ingest([{'customer_ref': 'd-1', 'first_name': 'Maria', 'customer': FIBER, 'account_state': None, 'error': None}], b, path=str(db))
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value('Review queue').run()
    assert not at.exception
    assert any(m.label == 'pending approval' and m.value == '1' for m in at.metric)
    assert any('Needs human approval' in w.value for w in at.warning)
    assert any('Twin scenarios' in h.value for h in at.subheader)             # the what-if appears without anyone filling a form
    approve = next(b_ for b_ in at.button if b_.label == 'Approve offer')
    assert approve.disabled                                        # nobody has typed a name yet
    next(t for t in at.text_input if t.key == 'q_who').set_value('Dana').run()
    next(b_ for b_ in at.button if b_.label == 'Approve offer').click().run()
    assert not at.exception
    case = pl.list_cases(path=str(db))[0]
    assert case['status'] == 'ready' and case['approved_by'] == 'Dana'


# ---------------------------------------------------------------- builder + file scoring
def test_make_coherent_forces_dependent_fields(bundle, customers):
    c = dl.to_model_row(customers.iloc[0], bundle['meta'])
    c.update(InternetService='No', PhoneService='No', MultipleLines='Yes', TechSupport='Yes')
    c = dl.make_coherent(c)
    assert c['TechSupport'] == 'No internet service' and c['MultipleLines'] == 'No phone service'
    from app import twin as tw
    tw.CustomerTwin(c, bundle['meta'])                    # does not raise


def test_score_file_scores_good_rows_and_explains_bad_ones(bundle, customers):
    df = customers.iloc[:20].reset_index()
    df.loc[3, 'Contract'] = 'Lifetime'                    # not an allowed level
    df.loc[5, ['InternetService', 'TechSupport']] = ['No', 'Yes']   # incoherent
    res = dl.score_file(bundle, df)
    assert len(res['scored']) == 18 and len(res['rejected']) == 2
    assert res['scored']['churn_probability'].between(0, 1).all()
    assert set(res['rejected']['row']) == {df.loc[3, 'customerID'], df.loc[5, 'customerID']}


def test_score_file_missing_column_is_an_error(bundle, customers):
    with pytest.raises(ValueError, match='Missing required columns'):
        dl.score_file(bundle, customers.iloc[:5].drop(columns=['tenure']).reset_index())


def test_builder_page_shows_score_and_decision():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value('Build a customer').run()
    assert not at.exception
    assert any(m.label == 'Calibrated churn probability' for m in at.metric)
    assert any('Decision:' in x.value for x in list(at.success) + list(at.warning) + list(at.info) + list(at.error))


def test_score_file_page_sample_toggle():
    at = AppTest.from_file(APP, default_timeout=120).run()
    at.sidebar.radio[0].set_value('Score a file').run()
    next(c for c in at.checkbox if c.key == 'sf_sample').check().run()
    assert not at.exception
    assert any(m.label == 'Scored' and m.value == '300' for m in at.metric)
