"""Receipts against a real instance: invoice with receipt, invoice document with two invoices."""

from datetime import date

import pytest

from nkfix_mcp.client import NkfClient
from nkfix_mcp.config import Config
from tests.contract.helpers import call, connected

pytestmark = pytest.mark.anyio

YEAR = date.today().year
# Smallest valid PDF with fantasy content; NebenkostenFix only checks the first bytes.
PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n'
       b'trailer<</Root 1 0 R>>\n%%EOF\n')


async def test_invoice_with_receipt_and_invoice_document(instance, tmp_path):
    (tmp_path / 'muell-fantasie.pdf').write_bytes(PDF)
    (tmp_path / 'stadtwerke-fantasie.pdf').write_bytes(PDF)
    async with connected(instance, 'erfassen', NKF_RECEIPT_DIR=str(tmp_path)) as mcp:
        house = next(i['id'] for i in (await call(mcp, 'list_properties'))['items'] if i['is_example'])
        cost_category = next(k for k in (await call(mcp, 'list_cost_categories'))['items'] if not k['requires_meter'])

        single = await call(mcp, 'record_invoice', property_id=house, cost_category_id=cost_category['id'],
                            amount='88,10', start_date=f'{YEAR}-01-01', end_date=f'{YEAR}-01-31',
                            invoice_number='BELEG-1', receipt_path='muell-fantasie.pdf')
        assert single['saved'] is True

        document = await call(mcp, 'upload_invoice_document', property_id=house,
                              receipt_path='stadtwerke-fantasie.pdf', description='Fantasie-Jahresrechnung')
        assert document['saved'] is True and document['id']
        for month, amount in (('02', '10.00'), ('03', '11.00')):
            part = await call(mcp, 'record_invoice', property_id=house, cost_category_id=cost_category['id'],
                              amount=amount, start_date=f'{YEAR}-{month}-01', end_date=f'{YEAR}-{month}-28',
                              invoice_document_id=document['id'])
            assert part['saved'] is True

        invoices = {r['id']: r for r in (await call(mcp, 'list_invoices', property_id=house, year=YEAR))['items']}
        assert invoices[single['id']]['has_receipt'] is True
        receipts = (await call(mcp, 'list_invoice_documents', property_id=house))['items']
        assert any(b['id'] == document['id'] and b['description'] == 'Fantasie-Jahresrechnung' for b in receipts)

        again = await call(mcp, 'upload_invoice_document', property_id=house, receipt_path='stadtwerke-fantasie.pdf')
        assert again['saved'] is False

    async with NkfClient(Config.from_env(instance.env('sitzung'))) as nkf:
        raw = await nkf.get('/api/invoice_documents')
    assert {'id', 'property_id', 'filename', 'description', 'upload_date'} <= set(raw[0])


async def test_disguised_receipt_never_leaves_the_computer(instance, tmp_path):
    (tmp_path / 'rechnung.pdf').write_text('<html>kein Beleg</html>')
    async with connected(instance, 'erfassen') as mcp:
        result = await mcp.call_tool('upload_invoice_document', {'property_id': 1,
                                                                  'receipt_path': str(tmp_path / 'rechnung.pdf')})
    assert result.is_error
    assert 'kein Beleg' in result.content[0].text
