import base64
import hashlib
import hmac
import struct

from app.security import generate_totp_secret, provisioning_uri, verify_totp


def totp(secret, timestamp):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", timestamp // 30), hashlib.sha1).digest()
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return f"{value % 1_000_000:06d}"


def test_totp_accepts_current_code_and_rejects_invalid_code():
    secret = generate_totp_secret()
    timestamp = 1_700_000_000

    assert verify_totp(secret, totp(secret, timestamp), now=timestamp)
    assert not verify_totp(secret, "000000", now=timestamp, window=0)
    assert not verify_totp(secret, "invalid", now=timestamp)


def test_totp_secret_uri_is_compatible_with_authenticator_apps():
    uri = provisioning_uri("JBSWY3DPEHPK3PXP", "gestor@example.com")

    assert uri.startswith("otpauth://totp/SIGI%20Gest%C3%A3o%3A")
    assert "secret=JBSWY3DPEHPK3PXP" in uri
    assert "digits=6&period=30" in uri
