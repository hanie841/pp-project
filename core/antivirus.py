"""Minimal ClamAV (clamd INSTREAM) client for scanning uploads before storing them."""
import logging
import socket
import struct
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024
TIMEOUT_SECONDS = 30


class ScanUnavailable(Exception):
    """clamd could not be reached or gave an unexpected reply."""


def _connect(address):
    parsed = urlparse(address)
    if parsed.scheme == 'unix' and hasattr(socket, 'AF_UNIX'):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target = parsed.path
    elif parsed.scheme == 'tcp':
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target = (parsed.hostname, parsed.port or 3310)
    else:
        raise ScanUnavailable(f'Unsupported CLAMD_ADDRESS: {address}')
    sock.settimeout(TIMEOUT_SECONDS)
    sock.connect(target)
    return sock


def _read_reply(sock):
    data = b''
    while not data.endswith(b'\0'):
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    return data.rstrip(b'\0').decode('utf-8', 'replace').strip()


def scan(uploaded_file):
    """Return True if clamd reports the file clean, False if it found malware.

    Raises ScanUnavailable when the scan could not be completed.
    """
    try:
        with _connect(settings.CLAMD_ADDRESS) as sock:
            sock.sendall(b'zINSTREAM\0')
            for chunk in uploaded_file.chunks(CHUNK_SIZE):
                sock.sendall(struct.pack('!L', len(chunk)) + chunk)
            sock.sendall(struct.pack('!L', 0))
            reply = _read_reply(sock)
    except OSError as exc:
        raise ScanUnavailable(str(exc)) from exc
    finally:
        uploaded_file.seek(0)

    if reply.endswith('OK'):
        return True
    if reply.endswith('FOUND'):
        logger.warning('Rejected infected upload %s: %s', uploaded_file.name, reply)
        return False
    raise ScanUnavailable(f'Unexpected clamd reply: {reply}')
