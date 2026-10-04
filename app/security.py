import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def verify_totp(secret, code, now=None, window=1):
    if not secret or not isinstance(code, str) or not code.isdigit() or len(code) != 6:
        return False
    try:
        key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    except (ValueError, TypeError):
        return False
    counter = int((time.time() if now is None else now) // 30)
    for offset in range(-window, window + 1):
        digest = hmac.new(key, struct.pack(">Q", counter + offset), hashlib.sha1).digest()
        index = digest[-1] & 0x0F
        value = struct.unpack(">I", digest[index:index + 4])[0] & 0x7FFFFFFF
        if hmac.compare_digest(f"{value % 1_000_000:06d}", code):
            return True
    return False


def provisioning_uri(secret, account, issuer="SIGI Gestão"):
    label = quote(f"{issuer}:{account}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"
