from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode


def _totp_at(
    secret_b32: str, t: float | int | None = None, step: int = 30, digits: int = 6
) -> str:
    """RFC 6238 TOTP code at time `t` (defaults to now)."""
    if t is None:
        t = int(time.time())
    counter = int(t) // step
    # Pad base32 to multiple of 8
    pad = "=" * ((8 - len(secret_b32) % 8) % 8)
    key = base64.b32decode(secret_b32.upper() + pad)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o : o + 4])[0] & 0x7FFFFFFF) % (10**digits)
    return str(code).zfill(digits)


def _normalize_totp_code(code: str) -> str:
    res = []
    for c in code:
        if c.isdigit():
            res.append(c)
    return "".join(res)


def _verify_totp_offsets(secret_b32: str, code: str, now: int, window: int) -> bool:
    for offset in range(-window, window + 1):
        if _totp_at(secret_b32, t=now + offset * 30) == code:
            return True
    return False


def _totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    if not secret_b32 or not code:
        return False
    normalized = _normalize_totp_code(code)
    if len(normalized) != 6:
        return False
    return _verify_totp_offsets(secret_b32, normalized, int(time.time()), window)


def _totp_new_secret() -> str:
    """Random 160-bit base32 secret (no padding)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_uri(secret_b32: str, account: str, issuer: str) -> str:
    """otpauth:// URI for QR code apps."""
    label = quote(f"{issuer}:{account}")
    params = urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": 6,
            "period": 30,
        }
    )
    return f"otpauth://totp/{label}?{params}"


def _generate_backup_codes(count: int = 10) -> list[str]:
    """Generate backup codes (8 chars alphanumeric, easy to type)."""
    codes = []
    for _ in range(count):
        # 8 chars: XXXX-XXXX format for readability
        code = "".join(
            secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8)
        )
        formatted = f"{code[:4]}-{code[4:]}"
        codes.append(formatted)
    return codes


def _hash_backup_code(code: str) -> str:
    """Hash a backup code with PBKDF2-SHA256."""
    # Remove hyphen for hashing
    code_clean = code.replace("-", "")
    salt = secrets.token_hex(16)
    hash_bytes = hashlib.pbkdf2_hmac(
        "sha256", code_clean.encode(), salt.encode(), 600000
    )
    return f"{salt}${hash_bytes.hex()}"


def _verify_backup_code(code: str, hashed: str) -> bool:
    """Verify a backup code against its hash."""
    code_clean = code.replace("-", "").upper()
    try:
        salt, expected_hash = hashed.split("$")
        hash_bytes = hashlib.pbkdf2_hmac(
            "sha256", code_clean.encode(), salt.encode(), 600000
        )
        return hmac.compare_digest(hash_bytes.hex(), expected_hash)
    except Exception:
        return False


def _generate_ctr_keystream(enc_key: bytes, iv: bytes, length: int) -> bytes:
    """Generate CTR mode keystream block-by-block using HMAC-SHA256."""
    keystream = bytearray()
    counter = 0
    while len(keystream) < length:
        block = hmac.new(
            enc_key, iv + counter.to_bytes(4, "big"), hashlib.sha256
        ).digest()
        keystream.extend(block)
        counter += 1
    return bytes(keystream[:length])


def _encrypt_totp_secret(secret: str, master_key: str) -> str:
    """Encrypt TOTP secret with CTR mode stream cipher + HMAC (stdlib only, zero-dep).

    Format: salt$iv$ciphertext$hmac
    - salt: 16 bytes hex (for key derivation)
    - iv: 16 bytes hex (initialization vector)
    - ciphertext: Encrypted secret
    - hmac: HMAC-SHA256 for integrity
    """
    # Generate salt and IV
    salt = secrets.token_bytes(16)
    iv = secrets.token_bytes(16)

    # Derive separate encryption and MAC keys from master key + salt (OWASP 2026: 600k iterations)
    derived_key = hashlib.pbkdf2_hmac(
        "sha256", master_key.encode(), salt, 600000, dklen=64
    )
    enc_key = derived_key[:32]
    mac_key = derived_key[32:]

    # CTR mode stream cipher encryption
    plaintext = secret.encode()
    keystream = _generate_ctr_keystream(enc_key, iv, len(plaintext))
    ciphertext = bytes(p ^ k for p, k in zip(plaintext, keystream))

    # HMAC for integrity (over salt + iv + ciphertext)
    mac = hmac.new(mac_key, salt + iv + ciphertext, hashlib.sha256).digest()

    # Encode and return
    return f"{salt.hex()}${iv.hex()}${ciphertext.hex()}${mac.hex()}"


def _decrypt_totp_secret_legacy(
    ciphertext: bytes, salt: bytes, iv: bytes, expected_mac: bytes, master_key: str
) -> str | None:
    """Fallback decryption for old repeated-key XOR encrypted secrets."""
    try:
        legacy_key = hashlib.pbkdf2_hmac("sha256", master_key.encode(), salt, 600000)
        legacy_mac = hmac.new(
            legacy_key, salt + iv + ciphertext, hashlib.sha256
        ).digest()
        if not hmac.compare_digest(legacy_mac, expected_mac):
            return None
        keystream = (legacy_key * ((len(ciphertext) // len(legacy_key)) + 1))[
            : len(ciphertext)
        ]
        plaintext = bytes(c ^ k for c, k in zip(ciphertext, keystream))
        return plaintext.decode()
    except Exception:
        return None


def _decrypt_totp_secret(encrypted: str, master_key: str) -> str | None:
    """Decrypt TOTP secret encrypted with _encrypt_totp_secret."""
    try:
        # Parse components
        parts = encrypted.split("$")
        if len(parts) != 4:
            return None

        salt = bytes.fromhex(parts[0])
        iv = bytes.fromhex(parts[1])
        ciphertext = bytes.fromhex(parts[2])
        expected_mac = bytes.fromhex(parts[3])

        # Derive keys (OWASP 2026: 600k iterations)
        derived_key = hashlib.pbkdf2_hmac(
            "sha256", master_key.encode(), salt, 600000, dklen=64
        )
        enc_key = derived_key[:32]
        mac_key = derived_key[32:]

        # Verify HMAC first to ensure integrity
        mac = hmac.new(mac_key, salt + iv + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(mac, expected_mac):
            # Fall back to legacy decryption scheme
            return _decrypt_totp_secret_legacy(
                ciphertext, salt, iv, expected_mac, master_key
            )

        # CTR mode stream cipher decryption (XOR is symmetric)
        keystream = _generate_ctr_keystream(enc_key, iv, len(ciphertext))
        plaintext = bytes(c ^ k for c, k in zip(ciphertext, keystream))

        return plaintext.decode()
    except Exception:
        return None


def _slugify_name(name: str) -> str:
    return name.lower() + "s"


def _humanize(name: str) -> str:
    out = []
    for i, c in enumerate(name):
        if i and c.isupper():
            out.append(" ")
        out.append(c)
    return "".join(out) + "s"


def _find_display_attribute(obj: Any) -> Optional[str]:
    for attr in ("name", "title", "label", "email", "username", "slug"):
        v = getattr(obj, attr, None)
        if v:
            return str(v)
    return None


def _is_generic_repr(s: str) -> bool:
    return s.startswith("<") and "id=" in s


def _display(obj: any) -> str:
    if obj is None:
        return ""
    import enum

    if isinstance(obj, enum.Enum):
        return str(obj.value)
    s = str(obj)
    if not _is_generic_repr(s):
        return s
    v = _find_display_attribute(obj)
    if v:
        return v
    return f"#{getattr(obj, 'id', '?')}"
