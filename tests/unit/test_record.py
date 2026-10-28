"""Recording: create, never change; on concerns the human decides, not the model.

The confirmation runs via MCP elicitation if the client supports it. If it
does not, the result is "not saved, confirmation needed", and only a second
call with the approval code from the first response saves. The code matches
exactly these arguments and concerns; setting it in advance or guessing fails.
"""

import re

import mcp_types as t
import pytest
from mcp.client import Client

from nkfix_mcp.server import build_server
from tests.helpers import FakeNkf, client, json_response
from tests.unit.test_read import ALLOWED_READ_ENDPOINTS

pytestmark = pytest.mark.anyio

RECORD_TOOLS = {'record_payment', 'record_meter_reading', 'record_invoice', 'upload_invoice_document'}

PAYMENT = {'id': 1, 'tenant_id': 1, 'tenant_name': 'Erika Muster', 'amount': 650.0,
           'payment_date': '2025-11-03', 'type': 'Miete'}
READING = {'id': 100, 'meter_id': 5, 'reading_date': '2025-09-30', 'value': 120.0, 'value_nt': None,
           'ablesungsart': 'ablesung', 'plausibility_status': 'ok'}
INVOICE = {'id': 7, 'property_id': 1, 'category_id': 3, 'amount': 412.5, 'start_date': '2025-01-01',
           'end_date': '2025-12-31', 'invoice_number': 'WA-2025-17', 'property_name': 'Musterhaus',
           'category_name': 'Wasser'}


def sample_data() -> FakeNkf:
    fake = FakeNkf()
    fake.json('GET', '/api/payments', [PAYMENT])
    fake.json('POST', '/api/payments', {'message': 'Zahlung erfolgreich erfasst', 'id': 42}, status=201)
    fake.json('GET', '/api/readings', [READING])
    fake.json('POST', '/api/readings/check_plausibility', {'status': 'ok', 'message': 'Keine zeitnahen Ablesungen.'})
    fake.json('POST', '/api/readings', {'id': 43}, status=201)
    fake.json('GET', '/api/invoices', [INVOICE])
    fake.json('POST', '/api/invoices', {'id': 44}, status=201)
    return fake


def answer_with(action: str, save: bool = True):
    questions: list[str] = []

    async def callback(context, params):
        questions.append(params.message)
        content = {'save': save} if action == 'accept' else None
        return t.ElicitResult(action=action, content=content)

    callback.questions = questions
    return callback


async def call(fake: FakeNkf, tool: str, callback=None, scope='erfassen', mode='auto', **arguments):
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, scope), elicitation_callback=callback, mode=mode) as mcp:
        result = await mcp.call_tool(tool, arguments)
    assert not result.is_error, result.content[0].text
    return result.structured_content


async def tools(scope, **values):
    nkf = client(FakeNkf(), **values)
    async with nkf, Client(build_server(nkf, scope)) as mcp:
        return {w.name: w for w in (await mcp.list_tools()).tools}


@pytest.mark.parametrize(('scope', 'offered'), [('lesen', False), ('erfassen', True), (None, True)])
async def test_record_tools_exist_only_for_access_that_may_record(scope, offered):
    names = set(await tools(scope))
    assert (RECORD_TOOLS <= names) is offered
    assert not (RECORD_TOOLS & names) if not offered else True


async def test_instructions_mention_recording_only_when_possible():
    nkf = client(FakeNkf())
    assert 'record_invoice' not in build_server(nkf, 'lesen').instructions
    assert 'record_invoice' in build_server(nkf, 'erfassen').instructions


async def test_record_tools_are_marked_as_writing_and_always_ask():
    all_tools = await tools('erfassen')
    for name in RECORD_TOOLS:
        hints = all_tools[name].annotations
        assert (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint) == (False, False, False)
        assert all_tools[name].meta == {'anthropic/requiresUserInteraction': True}
        assert 'schreibt, ändert nichts Bestehendes' in ' '.join(all_tools[name].description.split())


async def test_prompt_on_every_call_can_be_turned_off():
    all_tools = await tools('erfassen', NKF_NO_RECORD_PROMPT='1')
    assert all(not (all_tools[name].meta or {}).get('anthropic/requiresUserInteraction') for name in RECORD_TOOLS)


# --- Payment -----------------------------------------------------------------

async def test_payment_is_created():
    fake = sample_data()
    data = await call(fake, 'record_payment', tenant_id=1, amount='60', payment_date='2025-11-03',
                      payment_type='Nebenkostenvorauszahlung')
    assert data['saved'] is True and data['id'] == 42
    assert FakeNkf.body(fake.sent('POST', '/api/payments')[0]) == {
        'tenant_id': 1, 'amount': '60.00', 'type': 'Nebenkostenvorauszahlung', 'payment_date': '2025-11-03'}


async def test_unknown_payment_type_is_rejected_by_schema():
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_payment', {'tenant_id': 1, 'amount': '1', 'payment_date': '2025-11-03',
                                                        'payment_type': 'Trinkgeld'})
    assert result.is_error
    assert fake.sent('POST', '/api/payments') == []


async def test_duplicate_payment_without_elicitation_is_not_saved_but_asks_for_confirmation():
    fake = sample_data()
    data = await call(fake, 'record_payment', tenant_id=1, amount=650, payment_date='2025-11-03',
                      payment_type='Miete')
    assert data['saved'] is False
    assert data['id'] is None
    assert any('gibt es schon' in b for b in data['concerns'])
    assert re.fullmatch(r'[0-9a-f]{8}', data['approval_code'])
    assert f"approval_code='{data['approval_code']}'" in data['note']
    assert fake.sent('POST', '/api/payments') == []


DUPLICATE = {'tenant_id': 1, 'amount': 650, 'payment_date': '2025-11-03', 'payment_type': 'Miete'}


async def test_duplicate_payment_with_approval_code_is_saved_and_lists_concerns():
    fake = sample_data()
    code = (await call(fake, 'record_payment', **DUPLICATE))['approval_code']
    data = await call(fake, 'record_payment', **DUPLICATE, approval_code=code)
    assert data['saved'] is True
    assert any('gibt es schon' in b for b in data['concerns'])
    assert 'trotz Bedenken' in data['note']
    assert data['approval_code'] is None
    assert len(fake.sent('POST', '/api/payments')) == 1


@pytest.mark.parametrize('guessed', ['00000000', 'true', 'ja', 'bestätigt', ''])
async def test_code_set_in_advance_or_guessed_does_not_save(guessed):
    fake = sample_data()
    data = await call(fake, 'record_payment', **DUPLICATE, approval_code=guessed)
    assert data['saved'] is False
    assert 'passt nicht' in data['note']
    assert re.fullmatch(r'[0-9a-f]{8}', data['approval_code'])
    assert fake.sent('POST', '/api/payments') == []


async def test_approval_code_only_applies_to_the_same_values():
    fake = sample_data().json('GET', '/api/payments', [PAYMENT, {**PAYMENT, 'id': 2, 'amount': 700.0}])
    code = (await call(fake, 'record_payment', **DUPLICATE))['approval_code']
    data = await call(fake, 'record_payment', **{**DUPLICATE, 'amount': 700}, approval_code=code)
    assert data['saved'] is False
    assert fake.sent('POST', '/api/payments') == []


async def test_approval_code_no_longer_applies_when_new_concerns_appear():
    fake = sample_data()
    code = (await call(fake, 'record_payment', **DUPLICATE))['approval_code']
    fake.json('GET', '/api/payments', [PAYMENT, {**PAYMENT, 'id': 2}])
    data = await call(fake, 'record_payment', **DUPLICATE, approval_code=code)
    assert data['saved'] is False
    assert any('Zahlung 2' in b for b in data['concerns'])
    assert data['approval_code'] != code


async def test_approval_code_ignores_case_and_whitespace():
    fake = sample_data()
    code = (await call(fake, 'record_payment', **DUPLICATE))['approval_code']
    data = await call(fake, 'record_payment', **DUPLICATE, approval_code=f' {code.upper()} ')
    assert data['saved'] is True


async def test_approval_code_of_one_tool_does_not_apply_to_another():
    fake = sample_data().json('GET', '/api/readings', [{**READING, 'value': 900.0}])
    code = (await call(fake, 'record_payment', **DUPLICATE))['approval_code']
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=1,
                      approval_code=code)
    assert data['saved'] is False
    assert 'passt nicht' in data['note']


SAME_NUMBER = {'property_id': 1, 'cost_category_id': 9, 'amount': '100', 'start_date': '2025-01-01',
               'end_date': '2025-06-30', 'invoice_number': 'WA-2025-17'}


async def test_invoice_with_approval_code_is_saved():
    fake = sample_data()
    first = await call(fake, 'record_invoice', **SAME_NUMBER, base_price='12')
    assert first['saved'] is False and any('Nr. WA-2025-17' in b for b in first['concerns'])
    data = await call(fake, 'record_invoice', **SAME_NUMBER, base_price='12', approval_code=first['approval_code'])
    assert data['saved'] is True
    assert FakeNkf.body(fake.sent('POST', '/api/invoices')[0])['grundpreis'] == '12.00'


@pytest.mark.parametrize('changed', [{'amount': '200'}, {'base_price': '13'}, {'description': 'anders'},
                                     {'provider_id': 4}])
async def test_approval_code_does_not_apply_to_changed_values_with_the_same_concerns(changed):
    fake = sample_data()
    first = await call(fake, 'record_invoice', **SAME_NUMBER, base_price='12')
    data = await call(fake, 'record_invoice', **{**SAME_NUMBER, 'base_price': '12', **changed},
                      approval_code=first['approval_code'])
    assert data['concerns'] == first['concerns']
    assert data['saved'] is False and 'passt nicht' in data['note']
    assert fake.sent('POST', '/api/invoices') == []


# auto = protocol 2026-07-28 (InputRequiredResult), legacy = 2025-11-25 (request from the server).
PROTOCOLS = pytest.mark.parametrize('mode', ['auto', 'legacy'])


@PROTOCOLS
async def test_duplicate_payment_asks_the_human_if_the_client_can(mode):
    fake = sample_data()
    callback = answer_with('accept')
    data = await call(fake, 'record_payment', callback, mode=mode, tenant_id=1, amount=650,
                      payment_date='2025-11-03', payment_type='Miete')
    assert data['saved'] is True
    assert 'gibt es schon' in callback.questions[0]


@PROTOCOLS
@pytest.mark.parametrize(('action', 'save'), [('accept', False), ('decline', True), ('cancel', True)])
async def test_human_refusal_stands_even_if_the_model_sends_a_code(action, save, mode):
    fake = sample_data()
    data = await call(fake, 'record_payment', answer_with(action, save), mode=mode, tenant_id=1, amount=650,
                      payment_date='2025-11-03', payment_type='Miete', approval_code='00000000')
    assert data['saved'] is False
    assert 'abgelehnt' in data['note']
    assert fake.sent('POST', '/api/payments') == []


async def test_without_concerns_nobody_is_asked():
    callback = answer_with('decline')
    data = await call(sample_data(), 'record_payment', callback, tenant_id=1, amount=650, payment_date='2025-12-03',
                      payment_type='Miete')
    assert data['saved'] is True
    assert callback.questions == []


async def test_read_key_gets_clear_message_when_recording():
    # Scope unknown at startup: the tool exists, the instance answers 403.
    fake = sample_data().json('POST', '/api/payments', {'error': 'x', 'code': 'schluessel_umfang'}, status=403)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, None)) as mcp:
        result = await mcp.call_tool('record_payment', {'tenant_id': 1, 'amount': '5', 'payment_date': '2025-12-01',
                                                        'payment_type': 'Miete'})
    assert result.is_error
    assert 'Lesen und erfassen' in result.content[0].text


# --- Settlement payment and its statement --------------------------------------
# NebenkostenFix counts a settlement payment against a statement only if it
# carries billing_report_id; without it the statement stays "offen".

STATEMENT = {'id': 3, 'tenant_id': 1, 'tenant_name': 'Erika Muster', 'apartment_name': 'EG', 'property_id': 1,
             'property_name': 'Musterhaus', 'start_date': '2025-01-01', 'end_date': '2025-12-31',
             'created_at': '27.10.2026', 'veraltet': False, 'zugestellt_am': None, 'offen_betrag': '108.32',
             'zahlungsstand': 'offen'}
OTHER_TENANTS_STATEMENT = {**STATEMENT, 'id': 4, 'tenant_id': 2, 'tenant_name': 'Max Muster'}
SETTLEMENT = {'tenant_id': 1, 'amount': '108.32', 'payment_date': '2026-10-19', 'payment_type': 'Nebenkostenzahlung'}


def with_statements(*statements: dict) -> FakeNkf:
    return sample_data().json('GET', '/api/billing/reports', list(statements))


async def test_settlement_payment_is_linked_to_its_statement():
    fake = with_statements(STATEMENT, OTHER_TENANTS_STATEMENT)
    data = await call(fake, 'record_payment', **SETTLEMENT, statement_id=3)
    assert data['saved'] is True and data['concerns'] == []
    assert FakeNkf.body(fake.sent('POST', '/api/payments')[0]) == {
        'tenant_id': 1, 'amount': '108.32', 'type': 'Nebenkostenzahlung', 'payment_date': '2026-10-19',
        'billing_report_id': 3}


@pytest.mark.parametrize('payment_type', ['Miete', 'Nebenkostenvorauszahlung', 'Kaution'])
async def test_statement_id_with_other_payment_type_is_rejected(payment_type):
    # The API would silently drop billing_report_id for these types.
    fake = with_statements(STATEMENT)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_payment', {**SETTLEMENT, 'payment_type': payment_type,
                                                        'statement_id': 3})
    assert result.is_error
    assert 'nur bei Nebenkostenzahlung' in result.content[0].text
    assert fake.sent('POST', '/api/payments') == []


async def test_statement_of_another_tenant_is_rejected_before_asking():
    fake = with_statements(STATEMENT, OTHER_TENANTS_STATEMENT)
    callback = answer_with('accept')
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen'), elicitation_callback=callback) as mcp:
        result = await mcp.call_tool('record_payment', {**SETTLEMENT, 'statement_id': 4})
    assert result.is_error
    assert 'Abrechnung 4 gibt es für Mieter 1 nicht' in result.content[0].text
    assert callback.questions == []
    assert fake.sent('POST', '/api/payments') == []


async def test_settlement_payment_without_statement_raises_a_concern():
    fake = with_statements(STATEMENT, OTHER_TENANTS_STATEMENT)
    data = await call(fake, 'record_payment', **SETTLEMENT)
    assert data['saved'] is False
    [concern] = data['concerns']
    assert 'keiner Abrechnung zugeordnet' in concern
    assert 'Abrechnung 3 (01.01.2025 bis 31.12.2025, 108.32 € offen)' in concern
    assert 'Abrechnung 4' not in concern
    assert fake.sent('POST', '/api/payments') == []


async def test_settlement_payment_without_statement_is_fine_if_tenant_has_none():
    fake = with_statements(OTHER_TENANTS_STATEMENT)
    data = await call(fake, 'record_payment', **SETTLEMENT)
    assert data['saved'] is True and data['concerns'] == []


@pytest.mark.parametrize(('amount', 'difference'), [('135.00', '26.68 € mehr'), ('100.00', '8.32 € weniger')])
async def test_amount_other_than_outstanding_raises_a_concern(amount, difference):
    fake = with_statements(STATEMENT)
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': amount}, statement_id=3)
    assert data['saved'] is False
    assert data['concerns'] == [f'In Abrechnung 3 sind 108.32 € offen, die Zahlung ist {amount} € '
                                f'({difference}).']


async def test_settled_statement_raises_a_double_payment_concern():
    fake = with_statements({**STATEMENT, 'offen_betrag': '0.00', 'zahlungsstand': 'beglichen'})
    data = await call(fake, 'record_payment', **SETTLEMENT, statement_id=3)
    assert data['concerns'] == ['Abrechnung 3 ist schon beglichen; die Zahlung über 108.32 € wäre zu viel.']


CREDIT = {**STATEMENT, 'offen_betrag': '-42.10', 'zahlungsstand': 'offen'}


async def test_credit_payout_of_the_outstanding_credit_raises_no_concern():
    fake = with_statements(CREDIT)
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': '-42.10'}, statement_id=3)
    assert data['saved'] is True and data['concerns'] == []


async def test_credit_payout_with_wrong_sign_says_payouts_are_negative():
    fake = with_statements(CREDIT)
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': '42.10'}, statement_id=3)
    assert data['concerns'] == ['In Abrechnung 3 sind 42.10 € Guthaben auszuzahlen, die Zahlung ist aber positiv '
                                '(42.10 €). Eine Auszahlung wird negativ erfasst (-42.10).']


async def test_negative_payment_on_an_outstanding_amount_says_only_payouts_are_negative():
    fake = with_statements(STATEMENT)
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': '-108.32'}, statement_id=3)
    assert data['concerns'] == ['In Abrechnung 3 sind 108.32 € offen, die Zahlung ist aber negativ (-108.32 €). '
                                'Negativ wird nur ein ausgezahltes Guthaben erfasst.']


async def test_credit_payout_of_another_amount_names_the_difference():
    fake = with_statements(CREDIT)
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': '-50.00'}, statement_id=3)
    assert data['concerns'] == ['In Abrechnung 3 sind 42.10 € Guthaben auszuzahlen, die Auszahlung ist 50.00 € '
                                '(7.90 € mehr).']


async def test_unlinked_concern_names_a_credit_as_credit():
    fake = with_statements(CREDIT)
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': '-42.10'})
    assert 'Abrechnung 3 (01.01.2025 bis 31.12.2025, 42.10 € Guthaben auszuzahlen)' in data['concerns'][0]


async def test_statement_without_outstanding_amount_raises_no_amount_concern():
    # Old statements without a version have no payment status.
    fake = with_statements({**STATEMENT, 'offen_betrag': None, 'zahlungsstand': None})
    data = await call(fake, 'record_payment', **{**SETTLEMENT, 'amount': '135.00'}, statement_id=3)
    assert data['saved'] is True and data['concerns'] == []


async def test_approval_code_is_bound_to_the_statement():
    # The only concern is the duplicate, worded the same for both statements:
    # only statement_id itself tells the two calls apart.
    fake = with_statements(STATEMENT, {**STATEMENT, 'id': 5, 'start_date': '2024-07-01', 'end_date': '2024-12-31'})
    fake.json('GET', '/api/payments', [{**PAYMENT, 'amount': 108.32, 'payment_date': '2026-10-19',
                                        'type': 'Nebenkostenzahlung'}])
    first = await call(fake, 'record_payment', **SETTLEMENT, statement_id=3)
    assert first['concerns'] == ['Eine Zahlung über 108.32 € (Nebenkostenzahlung) vom 19.10.2026 gibt es schon für '
                                 'diesen Mieter (Zahlung 1).']
    data = await call(fake, 'record_payment', **SETTLEMENT, statement_id=5, approval_code=first['approval_code'])
    assert data['saved'] is False and 'passt nicht' in data['note']
    assert fake.sent('POST', '/api/payments') == []


async def test_other_payment_types_do_not_load_statements():
    fake = sample_data()
    await call(fake, 'record_payment', tenant_id=1, amount='60', payment_date='2025-11-03', payment_type='Miete')
    assert fake.sent('GET', '/api/billing/reports') == []


async def test_instructions_ask_for_the_statement_of_a_settlement_payment():
    instructions = build_server(client(FakeNkf()), 'erfassen').instructions
    assert 'statement_id' in instructions and 'list_statements' in instructions
    assert 'Guthabenauszahlung negativ' in instructions


async def test_payment_type_help_points_to_statement_id():
    record_payment = (await tools('erfassen'))['record_payment']
    payment_type_help = record_payment.input_schema['properties']['payment_type']['description']
    assert 'statement_id' in payment_type_help and 'Guthabenauszahlung negativ' in payment_type_help
    assert 'list_statements' in record_payment.input_schema['properties']['statement_id']['description']


# --- Meter reading -----------------------------------------------------------

async def test_unremarkable_meter_reading_is_saved_without_trotzdem():
    fake = sample_data()
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=131.5)
    assert data == {'saved': True, 'id': 43, 'concerns': [], 'note': 'Gespeichert.', 'approval_code': None}
    assert FakeNkf.body(fake.sent('POST', '/api/readings')[0]) == {
        'meter_id': 5, 'reading_date': '2025-12-31', 'value': 131.5, 'ablesungsart': 'ablesung'}


@pytest.mark.parametrize(('value', 'reading_date', 'concerns'), [
    (110.0, '2025-12-31', 'kleiner als der vorige'),
    (130.0, '2025-09-30', 'schon einen Stand (Stand 100)'),
])
async def test_backwards_or_duplicate_reading_asks_for_confirmation_first(value, reading_date, concerns):
    fake = sample_data()
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date=reading_date, value=value)
    assert data['saved'] is False
    assert any(concerns in b for b in data['concerns'])
    assert fake.sent('POST', '/api/readings') == []


async def test_plausibility_warning_asks_for_confirmation_and_once_confirmed_is_sent_with_trotzdem():
    fake = sample_data().json('POST', '/api/readings/check_plausibility',
                              {'status': 'warning', 'message': 'Achtung! Abweichung 900 %.',
                               'closest_date': '2025-09-30'})
    callback = answer_with('accept')
    data = await call(fake, 'record_meter_reading', callback, meter_id=5, reading_date='2025-10-10', value=1200)
    assert data['saved'] is True
    assert 'Abweichung 900 %' in callback.questions[0]
    assert FakeNkf.body(fake.sent('POST', '/api/readings')[0])['trotzdem'] is True


async def test_meter_reading_with_approval_code_is_sent_with_trotzdem():
    fake = sample_data()
    first = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=110)
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=110,
                      approval_code=first['approval_code'])
    assert data['saved'] is True
    assert FakeNkf.body(fake.sent('POST', '/api/readings')[0])['trotzdem'] is True


async def test_approval_code_does_not_apply_when_the_reading_type_changes():
    fake = sample_data()
    first = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=110)
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=110,
                      interim_reading=True, approval_code=first['approval_code'])
    assert data['saved'] is False


async def test_confirmation_request_from_instance_despite_precheck_is_passed_on():
    fake = sample_data().json('POST', '/api/readings', {'error': 'Der NT-Stand ist kleiner.', 'nachfrage': True},
                              status=409)
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=131.5,
                      value_off_peak=1)
    assert data['saved'] is False
    assert data['concerns'] == ['Der NT-Stand ist kleiner.']


async def test_interim_reading_on_tenant_change():
    fake = sample_data()
    await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=131.5,
               interim_reading=True)
    assert FakeNkf.body(fake.sent('POST', '/api/readings')[0])['ablesungsart'] == 'zwischenablesung'


# --- Invoice -----------------------------------------------------------------

async def test_invoice_is_created_with_the_api_fields():
    fake = sample_data()
    data = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='98.7',
                      start_date='2025-01-01', end_date='2025-12-31', invoice_number='MUELL-25',
                      invoice_date='2026-01-05', description='Müllabfuhr', provider_id=4)
    assert data['saved'] is True and data['id'] == 44
    assert FakeNkf.body(fake.sent('POST', '/api/invoices')[0]) == {
        'property_id': 1, 'category_id': 3, 'amount': '98.70', 'start_date': '2025-01-01', 'end_date': '2025-12-31',
        'invoice_number': 'MUELL-25', 'rechnungsdatum': '2026-01-05', 'description': 'Müllabfuhr',
        'provider_id': 4}


@pytest.mark.parametrize('arguments', [
    {'invoice_number': 'WA-2025-17', 'amount': '1', 'start_date': '2025-03-01', 'end_date': '2025-03-31'},
    {'amount': '412.50', 'start_date': '2025-01-01', 'end_date': '2025-12-31'},
])
async def test_existing_invoice_asks_for_confirmation(arguments):
    fake = sample_data()
    data = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, **arguments)
    assert data['saved'] is False
    assert any('Rechnung 7' in b for b in data['concerns'])
    assert fake.sent('POST', '/api/invoices') == []


async def test_heating_invoice_includes_heating_system_and_items():
    fake = sample_data()
    await call(fake, 'record_invoice', property_id=1, cost_category_id=5, amount='1500', start_date='2025-01-01',
               end_date='2025-12-31', heating_system_id=2, heating_cost_type='brennstoff', co2_cost='120.5')
    body = FakeNkf.body(fake.sent('POST', '/api/invoices')[0])
    assert (body['heizungsanlage_id'], body['heizkostenart'], body['co2_kosten']) == (2, 'brennstoff', '120.50')
    assert 'rechnungsdatum_fuer_beleg' not in body


async def test_end_date_before_start_date_is_rejected_before_sending():
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_invoice', {'property_id': 1, 'cost_category_id': 3, 'amount': '5',
                                                        'start_date': '2025-12-31', 'end_date': '2025-01-01'})
    assert result.is_error
    assert fake.sent('POST', '/api/invoices') == []


ALLOWED_RECORD_ENDPOINTS = ALLOWED_READ_ENDPOINTS + [
    r'POST /api/invoices', r'POST /api/invoice_documents', r'POST /api/readings', r'POST /api/payments',
]


async def test_record_guard_calls_only_allowed_endpoints(tmp_path):
    (tmp_path / 'b.pdf').write_bytes(b'%PDF-1.7 Fantasie')
    fake = sample_data()
    fake.json('GET', '/api/invoice_documents', [])
    fake.json('POST', '/api/invoice_documents', {'id': 9, 'filename': 'b.pdf'}, status=201)
    for tool, arguments in [
        ('upload_invoice_document', {'property_id': 1, 'receipt_path': str(tmp_path / 'b.pdf')}),
        ('record_invoice', {'property_id': 1, 'cost_category_id': 3, 'amount': '7', 'start_date': '2025-02-01',
                            'end_date': '2025-02-28', 'receipt_path': str(tmp_path / 'b.pdf')}),
        ('record_payment', {'tenant_id': 1, 'amount': '1', 'payment_date': '2025-12-01', 'payment_type': 'Miete'}),
        ('record_meter_reading', {'meter_id': 5, 'reading_date': '2025-12-31', 'value': 200}),
        ('record_invoice', {'property_id': 1, 'cost_category_id': 3, 'amount': '5', 'start_date': '2025-01-01',
                            'end_date': '2025-01-31'}),
    ]:
        await call(fake, tool, **arguments)
    for request in fake.requests:
        request_line = f'{request.method} {request.url.path}'
        assert any(re.fullmatch(pattern, request_line) for pattern in ALLOWED_RECORD_ENDPOINTS), request_line
    assert not any(a.method in ('PUT', 'PATCH', 'DELETE') for a in fake.requests)
    # AENDERT_BESTEHENDES in NebenkostenFix: never send it, neither as JSON nor as a form field.
    posts = [a for a in fake.requests if a.method == 'POST']
    assert {a.url.path for a in posts} >= {'/api/invoices', '/api/invoice_documents', '/api/payments', '/api/readings'}
    assert all(b'rechnungsdatum_fuer_beleg' not in a.content for a in posts)


async def test_money_with_decimal_comma_is_understood():
    fake = sample_data()
    await call(fake, 'record_payment', tenant_id=1, amount='1234,5', payment_date='2025-12-01', payment_type='Kaution')
    assert FakeNkf.body(fake.sent('POST', '/api/payments')[0])['amount'] == '1234.50'


async def test_invoice_response_without_id_does_not_crash():
    # Response 201 without id (e.g. an older instance): no crash.
    fake = sample_data().set_route('POST', '/api/invoices', json_response(201, {}))
    data = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='5', start_date='2025-02-01',
                      end_date='2025-02-28')
    assert data['saved'] is True and data['id'] is None


@pytest.mark.parametrize(('raw', 'expected'), [
    ('1500', '1500.00'), ('1.500,00', '1500.00'), ('1500,5', '1500.50'), (98.7, '98.70'), ('12 €', '12.00'),
])
async def test_unambiguous_amounts(raw, expected):
    fake = sample_data()
    await call(fake, 'record_payment', tenant_id=1, amount=raw, payment_date='2025-12-01', payment_type='Kaution')
    assert FakeNkf.body(fake.sent('POST', '/api/payments')[0])['amount'] == expected


@pytest.mark.parametrize('raw', ['1.500', '12,345', '0.005', 'NaN', 'zwölf', True])
async def test_ambiguous_or_invalid_amounts_are_rejected(raw):
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_payment', {'tenant_id': 1, 'amount': raw, 'payment_date': '2025-12-01',
                                                        'payment_type': 'Kaution'})
    assert result.is_error
    assert fake.sent('POST', '/api/payments') == []


async def test_large_readings_are_stated_exactly():
    fake = sample_data().json('GET', '/api/readings', [{**READING, 'value': 1234567.25}])
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=1234567.2)
    assert any('1234567,2 ist kleiner als der vorige' in b and '(1234567,25)' in b for b in data['concerns'])


async def test_backwards_off_peak_reading_asks_for_confirmation():
    fake = sample_data().json('GET', '/api/readings', [{**READING, 'value_nt': 50.0}])
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=130,
                      value_off_peak=40)
    assert data['saved'] is False
    assert any(b.startswith('Der NT-Stand 40') for b in data['concerns'])


async def test_unexpected_confirmation_request_from_instance_does_not_loop():
    fake = sample_data().json('POST', '/api/readings', {'error': 'Unbekannte Rückfrage.', 'nachfrage': True},
                              status=409)
    data = await call(fake, 'record_meter_reading', meter_id=5, reading_date='2025-12-31', value=131.5)
    assert data['saved'] is False
    assert 'selbst eintragen' in data['note']
    assert data['approval_code'] is None and 'approval_code' not in data['note']


async def test_labor_cost_requires_section_35a_category():
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_invoice', {
            'property_id': 1, 'cost_category_id': 3, 'amount': '300', 'start_date': '2025-01-01',
            'end_date': '2025-12-31', 'labor_cost': '120'})
    assert result.is_error and '35a' in result.content[0].text
    data = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='300', start_date='2025-01-01',
                      end_date='2025-12-31', labor_cost='120', section_35a_category='haushaltsnah')
    body = FakeNkf.body(fake.sent('POST', '/api/invoices')[0])
    assert data['saved'] and (body['art_35a'], body['lohnanteil']) == ('haushaltsnah', '120.00')


async def test_same_invoice_for_another_apartment_is_not_a_duplicate():
    fake = sample_data()
    fake.json('GET', '/api/properties/1/apartments', [{'id': 10, 'name': 'EG'}, {'id': 11, 'name': 'OG'}])
    fake.json('GET', '/api/invoices', [{**INVOICE, 'invoice_number': None, 'apartment_id': 10}])
    data = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='412.50',
                      start_date='2025-01-01', end_date='2025-12-31', apartment_id=11, invoice_number='   ')
    assert data['saved'] is True
    assert 'invoice_number' not in FakeNkf.body(fake.sent('POST', '/api/invoices')[0])


async def test_errors_come_before_asking_the_human():
    callback = answer_with('accept')
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen'), elicitation_callback=callback) as mcp:
        result = await mcp.call_tool('record_invoice', {
            'property_id': 1, 'cost_category_id': 3, 'amount': '412.50', 'start_date': '2025-12-31',
            'end_date': '2025-01-01'})
    assert result.is_error
    assert callback.questions == []


@pytest.mark.parametrize(('field', 'value', 'message'), [
    ('invoice_document_id', 4, 'Sammelbeleg 4 gehört nicht'),
    ('apartment_id', 20, 'Wohnung 20 gehört nicht'),
])
async def test_invoice_document_and_apartment_must_belong_to_the_property(field, value, message):
    fake = sample_data()
    fake.json('GET', '/api/invoice_documents', [{'id': 4, 'property_id': 2, 'filename': 'x.pdf'}])
    fake.json('GET', '/api/properties/1/apartments', [{'id': 10, 'name': 'EG'}])
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_invoice', {
            'property_id': 1, 'cost_category_id': 3, 'amount': '5', 'start_date': '2025-01-01',
            'end_date': '2025-01-31', field: value})
    assert result.is_error and message in result.content[0].text


async def test_the_question_lists_the_new_values():
    callback = answer_with('decline')
    await call(sample_data(), 'record_invoice', callback, property_id=1, cost_category_id=3, amount='1',
               start_date='2025-03-01', end_date='2025-03-31', invoice_number='WA-2025-17')
    assert 'Neue Rechnung: Kostenart 3, 1.00 €, 2025-03-01 bis 2025-03-31, Nr. WA-2025-17' in callback.questions[0]
    assert 'Rechnung 7' in callback.questions[0]


async def test_timeout_while_saving_says_the_outcome_is_unclear():
    import httpx2

    def slow(request):
        raise httpx2.ReadTimeout('zu langsam', request=request)

    fake = sample_data().set_route('POST', '/api/payments', slow)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('record_payment', {'tenant_id': 1, 'amount': '5', 'payment_date': '2025-12-01',
                                                        'payment_type': 'Miete'})
    assert result.is_error and 'unklar' in result.content[0].text


@pytest.mark.parametrize(('tool', 'arguments'), [
    ('record_payment', {'tenant_id': 1, 'amount': '1', 'payment_date': '2025-13-45', 'payment_type': 'Miete'}),
    ('record_meter_reading', {'meter_id': 5, 'reading_date': '2025-02-30', 'value': 1}),
    ('record_invoice', {'property_id': 1, 'cost_category_id': 3, 'amount': '1', 'start_date': '2025-01-01',
                        'end_date': '2025-02-30'}),
    ('record_invoice', {'property_id': 1, 'cost_category_id': 3, 'amount': '1', 'start_date': '2025-01-01',
                        'end_date': '2025-12-31', 'invoice_date': '2026-00-01'}),
])
async def test_impossible_date_is_rejected_clearly_and_not_sent(tool, arguments):
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool(tool, arguments)
    assert result.is_error
    assert 'kein gültiges Datum' in result.content[0].text
    assert not fake.requests


@pytest.mark.parametrize(('path', 'tool', 'arguments'), [
    ('/api/payments', 'record_payment', {'tenant_id': 1, 'amount': '1', 'payment_date': '2025-12-01',
                                         'payment_type': 'Miete'}),
    ('/api/readings/check_plausibility', 'record_meter_reading', {'meter_id': 5, 'reading_date': '2025-12-31',
                                                                  'value': 131}),
    ('/api/invoices', 'record_invoice', {'property_id': 1, 'cost_category_id': 3, 'amount': '1',
                                         'start_date': '2025-01-01', 'end_date': '2025-12-31'}),
])
async def test_instance_error_in_the_precheck_reaches_the_model_with_text(path, tool, arguments):
    method = 'POST' if 'check' in path else 'GET'
    fake = sample_data().json(method, path, {'error': 'Datenbank gesperrt.'}, status=500)
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool(tool, arguments)
    assert result.is_error
    assert 'Datenbank gesperrt' in result.content[0].text


async def test_missing_invoice_document_in_the_precheck_reaches_the_model_with_text(tmp_path):
    fake = sample_data()
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('upload_invoice_document', {'property_id': 1,
                                                                 'receipt_path': str(tmp_path / 'fehlt.pdf')})
    assert result.is_error and 'Beleg nicht gefunden' in result.content[0].text
