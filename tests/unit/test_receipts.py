"""Receipts: check a local file and upload it as multipart."""

import pytest
from mcp.client import Client

from nkfix_mcp.receipts import ReceiptError, read_receipt
from nkfix_mcp.server import build_server
from tests.helpers import FakeNkf, client

pytestmark = pytest.mark.anyio

PDF = b'%PDF-1.7\n% Fantasiebeleg\n' + b'0' * 100
HEADERS = {
    'pdf': (PDF, 'application/pdf'),
    'png': (b'\x89PNG\r\n\x1a\n' + b'0' * 40, 'image/png'),
    'jpg': (b'\xff\xd8\xff\xe0' + b'0' * 40, 'image/jpeg'),
    'webp': (b'RIFF\x00\x00\x00\x00WEBPVP8 ' + b'0' * 40, 'image/webp'),
    'heic': (b'\x00\x00\x00\x18ftypheic' + b'0' * 40, 'image/heic'),
}


@pytest.mark.parametrize('file_type', sorted(HEADERS))
def test_allowed_receipts_are_detected_by_content(tmp_path, file_type):
    content, mime = HEADERS[file_type]
    file = tmp_path / f'beleg.{file_type}'
    file.write_bytes(content)
    receipt = read_receipt(str(file), directory=None)
    assert (receipt.filename, receipt.content, receipt.mime) == (f'beleg.{file_type}', content, mime)


def test_disguised_html_is_rejected(tmp_path):
    file = tmp_path / 'rechnung.pdf'
    file.write_bytes(b'<html><script>alert(1)</script></html>')
    with pytest.raises(ReceiptError, match='PDF, JPEG, PNG, WebP oder HEIC'):
        read_receipt(str(file), directory=None)


def test_missing_file_and_directory(tmp_path):
    with pytest.raises(ReceiptError, match='nicht gefunden'):
        read_receipt(str(tmp_path / 'fehlt.pdf'), directory=None)
    with pytest.raises(ReceiptError, match='keine Datei'):
        read_receipt(str(tmp_path), directory=None)


def test_file_too_large(tmp_path, monkeypatch):
    monkeypatch.setattr('nkfix_mcp.receipts.MAX_RECEIPT_BYTES', 50)
    file = tmp_path / 'gross.pdf'
    file.write_bytes(PDF)
    with pytest.raises(ReceiptError, match='zu groß'):
        read_receipt(str(file), directory=None)


def test_tilde_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    (tmp_path / 'b.pdf').write_bytes(PDF)
    assert read_receipt('~/b.pdf', directory=None).filename == 'b.pdf'


def test_receipt_dir_applies_strictly_even_via_dotdot_and_symlinks(tmp_path):
    directory = tmp_path / 'belege'
    directory.mkdir()
    (directory / 'ok.pdf').write_bytes(PDF)
    outside = tmp_path / 'geheim.pdf'
    outside.write_bytes(PDF)
    (directory / 'link.pdf').symlink_to(outside)

    assert read_receipt(str(directory / 'ok.pdf'), directory=directory.resolve()).filename == 'ok.pdf'
    for path in (outside, directory / '..' / 'geheim.pdf', directory / 'link.pdf'):
        with pytest.raises(ReceiptError, match='NKF_RECEIPT_DIR'):
            read_receipt(str(path), directory=directory.resolve())


def test_relative_path_resolves_in_receipt_dir(tmp_path):
    (tmp_path / 'rel.pdf').write_bytes(PDF)
    assert read_receipt('rel.pdf', directory=tmp_path.resolve()).filename == 'rel.pdf'


# --- Tools -------------------------------------------------------------------

def sample_data() -> FakeNkf:
    fake = FakeNkf()
    fake.json('GET', '/api/invoices', [])
    fake.json('POST', '/api/invoices', {'id': 44}, status=201)
    fake.json('GET', '/api/invoice_documents', [
        {'id': 3, 'property_id': 1, 'filename': 'Stadtwerke-2025.pdf', 'description': None}])
    fake.json('POST', '/api/invoice_documents', {'id': 9, 'filename': 'sammel.pdf'}, status=201)
    return fake


async def call(fake, tool, **arguments):
    nkf = client(fake)
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool(tool, arguments)
    return result


def multipart(request) -> tuple[dict[str, str], dict[str, tuple[str, str, bytes]]]:
    """Form fields and files from a multipart request."""
    from email.parser import BytesParser
    from email.policy import default

    head = f'Content-Type: {request.headers["Content-Type"]}\r\n\r\n'.encode()
    message = BytesParser(policy=default).parsebytes(head + request.content)
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param('name', header='content-disposition')
        if part.get_filename():
            files[name] = (part.get_filename(), part.get_content_type(), part.get_payload(decode=True))
        else:
            fields[name] = part.get_content()
    return fields, files


async def test_invoice_with_receipt_is_sent_as_multipart(tmp_path):
    (tmp_path / 'wasser.pdf').write_bytes(PDF)
    fake = sample_data()
    result = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='412,50',
                        start_date='2025-01-01', end_date='2025-12-31', invoice_number='WA-1',
                        receipt_path=str(tmp_path / 'wasser.pdf'))
    assert not result.is_error, result.content[0].text
    assert result.structured_content['saved'] is True
    fields, files = multipart(fake.sent('POST', '/api/invoices')[0])
    assert fields == {'property_id': '1', 'category_id': '3', 'amount': '412.50', 'start_date': '2025-01-01',
                      'end_date': '2025-12-31', 'invoice_number': 'WA-1'}
    assert files == {'file': ('wasser.pdf', 'application/pdf', PDF)}


async def test_receipt_and_invoice_document_together_are_rejected(tmp_path):
    (tmp_path / 'x.pdf').write_bytes(PDF)
    fake = sample_data()
    result = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='1', start_date='2025-01-01',
                        end_date='2025-01-31', receipt_path=str(tmp_path / 'x.pdf'), invoice_document_id=3)
    assert result.is_error
    assert 'entweder' in result.content[0].text.lower()
    assert fake.sent('POST', '/api/invoices') == []


async def test_broken_receipt_is_rejected_before_any_request(tmp_path):
    (tmp_path / 'boese.pdf').write_text('<svg onload=alert(1)>')
    fake = sample_data()
    result = await call(fake, 'record_invoice', property_id=1, cost_category_id=3, amount='1', start_date='2025-01-01',
                        end_date='2025-01-31', receipt_path=str(tmp_path / 'boese.pdf'))
    assert result.is_error
    assert 'PDF' in result.content[0].text
    assert not [a for a in fake.requests if a.method == 'POST']


async def test_upload_invoice_document(tmp_path):
    (tmp_path / 'Jahresabrechnung Stadtwerke.pdf').write_bytes(PDF)
    fake = sample_data()
    result = await call(fake, 'upload_invoice_document', property_id=1,
                        receipt_path=str(tmp_path / 'Jahresabrechnung Stadtwerke.pdf'), description='Strom und Gas')
    assert not result.is_error, result.content[0].text
    assert result.structured_content == {'saved': True, 'id': 9, 'concerns': [], 'note': (
        'Gespeichert. Lege die Rechnungen dieses Belegs mit record_invoice und invoice_document_id=9 an.'),
        'approval_code': None}
    fields, files = multipart(fake.sent('POST', '/api/invoice_documents')[0])
    assert fields == {'property_id': '1', 'description': 'Strom und Gas'}
    assert files['file'][0] == 'Jahresabrechnung Stadtwerke.pdf'


async def test_invoice_document_with_same_name_asks_for_confirmation(tmp_path):
    (tmp_path / 'stadtwerke-2025.PDF').write_bytes(PDF)
    fake = sample_data()
    result = await call(fake, 'upload_invoice_document', property_id=1,
                        receipt_path=str(tmp_path / 'stadtwerke-2025.PDF'))
    data = result.structured_content
    assert data['saved'] is False
    assert any('Sammelbeleg 3' in b for b in data['concerns'])
    assert fake.sent('POST', '/api/invoice_documents') == []


async def test_approval_code_of_an_invoice_document_only_applies_to_the_same_content(tmp_path):
    file = tmp_path / 'stadtwerke-2025.pdf'
    file.write_bytes(PDF)
    fake = sample_data()
    first = (await call(fake, 'upload_invoice_document', property_id=1, receipt_path=str(file))).structured_content
    file.write_bytes(PDF + b'anders')
    second = await call(fake, 'upload_invoice_document', property_id=1, receipt_path=str(file),
                        approval_code=first['approval_code'])
    assert second.structured_content['saved'] is False
    third = await call(fake, 'upload_invoice_document', property_id=1, receipt_path=str(file),
                       approval_code=second.structured_content['approval_code'])
    assert third.structured_content['saved'] is True
    assert len(fake.sent('POST', '/api/invoice_documents')) == 1


async def test_receipt_dir_from_config_applies(tmp_path):
    directory = tmp_path / 'belege'
    directory.mkdir()
    (tmp_path / 'draussen.pdf').write_bytes(PDF)
    fake = sample_data()
    nkf = client(fake, NKF_RECEIPT_DIR=str(directory))
    async with nkf, Client(build_server(nkf, 'erfassen')) as mcp:
        result = await mcp.call_tool('upload_invoice_document', {'property_id': 1,
                                                                 'receipt_path': str(tmp_path / 'draussen.pdf')})
    assert result.is_error
    assert 'NKF_RECEIPT_DIR' in result.content[0].text


async def test_upload_invoice_document_is_a_record_tool():
    nkf = client(FakeNkf())
    async with nkf, Client(build_server(nkf, 'lesen')) as mcp:
        assert 'upload_invoice_document' not in {w.name for w in (await mcp.list_tools()).tools}
    async with Client(build_server(nkf, 'erfassen')) as mcp:
        tool = next(w for w in (await mcp.list_tools()).tools if w.name == 'upload_invoice_document')
    assert tool.annotations.read_only_hint is False
    assert tool.meta == {'anthropic/requiresUserInteraction': True}


def test_name_core_as_nebenkostenfix_stores_it():
    from nkfix_mcp.tools.record import _name_core
    assert _name_core('/x/Stadtwerke-Gäste 2025.pdf') == _name_core('Stadtwerke_Gaeste_2025.pdf')
    assert _name_core('a.pdf') != _name_core('b.pdf')


def test_missing_read_permission_is_reported_clearly(tmp_path):
    file = tmp_path / 'gesperrt.pdf'
    file.write_bytes(PDF)
    file.chmod(0)
    try:
        with pytest.raises(ReceiptError, match='Kein Lesezugriff'):
            read_receipt(str(file), directory=None)
    finally:
        file.chmod(0o600)


def test_symlink_loop_is_reported_clearly(tmp_path):
    (tmp_path / 'a.pdf').symlink_to(tmp_path / 'b.pdf')
    (tmp_path / 'b.pdf').symlink_to(tmp_path / 'a.pdf')
    with pytest.raises(ReceiptError, match='nicht lesbar'):
        read_receipt(str(tmp_path / 'a.pdf'), directory=None)


def test_without_receipt_dir_messages_reveal_neither_size_nor_link_target(tmp_path, monkeypatch):
    monkeypatch.setattr('nkfix_mcp.receipts.MAX_RECEIPT_BYTES', 50)
    target = tmp_path / 'geheim' / 'steuer-2025.pdf'
    target.parent.mkdir()
    target.write_bytes(PDF)
    (tmp_path / 'harmlos.pdf').symlink_to(target)
    with pytest.raises(ReceiptError) as error:
        read_receipt(str(tmp_path / 'harmlos.pdf'), directory=None)
    assert 'geheim' not in str(error.value)
    assert 'MB' not in str(error.value).split('höchstens')[0]


def test_growing_file_is_not_read_without_limit(tmp_path, monkeypatch):
    monkeypatch.setattr('nkfix_mcp.receipts.MAX_RECEIPT_BYTES', 64)
    file = tmp_path / 'gross.pdf'
    file.write_bytes(PDF + b'0' * 1000)
    with pytest.raises(ReceiptError, match='zu groß'):
        read_receipt(str(file), directory=None)
