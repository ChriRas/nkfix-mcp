"""Read receipts from disk before they go to NebenkostenFix.

NebenkostenFix accepts PDF, JPEG, PNG, WebP and HEIC and detects the type by
content, not by extension (``nebenkostenfix/uploads.py``). The same check
runs here beforehand: the model gets a clear message instead of a 400, and a
file that is not a receipt never leaves the machine at all.

The server reads any file whose path the model names. If
``NKF_RECEIPT_DIR`` is set, only files inside it, after resolving ``..`` and
symbolic links. Without a directory, messages name only the path the model
sent: neither size nor the target of a link, otherwise this could be used to
probe the file system.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

# Default of NebenkostenFix (MAX_UPLOAD_MB). An instance with a smaller
# limit answers 413; errors.py handles that.
MAX_RECEIPT_BYTES = 32 * 1024 * 1024
SNIFF_BYTES = 32
_HEIF_BRANDS = frozenset({b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'hevm', b'hevs', b'mif1', b'msf1'})


class ReceiptError(Exception):
    """The file is not usable as a receipt; the text is meant for the model."""


@dataclass(frozen=True)
class Receipt:
    filename: str
    content: bytes
    mime: str

    def as_upload(self) -> dict[str, tuple[str, bytes, str]]:
        return {'file': (self.filename, self.content, self.mime)}


def sniff_mime(head: bytes) -> str | None:
    """MIME type from the first bytes, like ``uploads.erkenne`` in NebenkostenFix."""
    if head.startswith(b'%PDF-'):
        return 'application/pdf'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    if head[4:8] == b'ftyp' and head[8:12] in _HEIF_BRANDS:
        return 'image/heic'
    return None


def read_receipt(path: str, directory: Path | None) -> Receipt:
    raw = Path(path).expanduser()
    if directory is not None and not raw.is_absolute():
        raw = directory / raw
    # Without a directory, every message names only the path as it came.
    shown_path = str(raw) if directory is None else None
    try:
        file = raw.resolve(strict=True)
    except FileNotFoundError:
        raise ReceiptError(f'Beleg nicht gefunden: {shown_path or path}') from None
    except (OSError, RuntimeError) as error:
        raise ReceiptError(f'Beleg nicht lesbar: {shown_path or path} ({type(error).__name__}).') from None
    if directory is not None and not file.is_relative_to(directory):
        raise ReceiptError(f'{path} liegt nicht im freigegebenen Belegordner (NKF_RECEIPT_DIR = {directory}).')
    shown_path = shown_path or str(file)

    # Open once and do everything through this descriptor: between checking
    # and reading, nobody should be able to swap the file or make it grow.
    try:
        fd = os.open(file, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except PermissionError:
        raise ReceiptError(f'Kein Lesezugriff auf {shown_path}. Unter macOS braucht der Prozess, der den MCP-Server '
                           'startet, Zugriff auf diesen Ordner (Systemeinstellungen → Datenschutz & Sicherheit), '
                           'oder leg den Beleg in den Belegordner (NKF_RECEIPT_DIR).') from None
    except OSError as error:
        raise ReceiptError(f'Beleg nicht lesbar: {shown_path} ({type(error).__name__}).') from None
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ReceiptError(f'{shown_path} ist keine Datei.')
    with os.fdopen(fd, 'rb') as stream:
        content = stream.read(MAX_RECEIPT_BYTES + 1)
    if len(content) > MAX_RECEIPT_BYTES:
        raise ReceiptError(f'{file.name} ist zu groß (höchstens {MAX_RECEIPT_BYTES // 1024 // 1024} MB).')
    mime = sniff_mime(content[:SNIFF_BYTES])
    if mime is None:
        raise ReceiptError(f'{file.name} ist kein Beleg, den NebenkostenFix annimmt. Erlaubt sind PDF, JPEG, PNG, '
                           'WebP oder HEIC; erkannt wird am Inhalt, nicht an der Endung.')
    return Receipt(filename=file.name, content=content, mime=mime)
