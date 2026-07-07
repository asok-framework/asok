import sys
from unittest.mock import MagicMock

# Mock cryptography if not installed to prevent ModuleNotFoundError in tests
try:
    from cryptography.fernet import Fernet  # noqa: F401
except ImportError:

    class MockFernet:
        def __init__(self, key):
            self.key = key if isinstance(key, bytes) else key.encode()

        def encrypt(self, data: bytes) -> bytes:
            import base64

            # Embed key in token to simulate validation
            payload = self.key + b":" + data
            return b"gAAAAA" + base64.b64encode(payload)

        def decrypt(self, token: bytes) -> bytes:
            import base64

            token_bytes = token if isinstance(token, bytes) else token.encode()
            if token_bytes.startswith(b"gAAAAA"):
                decoded = base64.b64decode(token_bytes[6:])
                key, data = decoded.split(b":", 1)
                if key != self.key:
                    raise Exception("Invalid key")
                return data
            return token_bytes

    mock_crypto = MagicMock()
    mock_crypto.fernet.Fernet = MockFernet
    sys.modules["cryptography"] = mock_crypto
    sys.modules["cryptography.fernet"] = mock_crypto.fernet

import pytest

from asok.orm import Field, Model


class SecureUser(Model):
    name = Field.String()
    ssn = Field.EncryptedString()
    api_key = Field.EncryptedString()


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    SecureUser.close_connections()
    monkeypatch.setattr(SecureUser, "_db_path", db_path)
    # Set a default test key
    monkeypatch.setenv("SECRET_KEY", "super-secret-key-for-testing-purposes-only")
    SecureUser.create_table()
    yield
    SecureUser.close_connections()


def test_encrypted_field_flow():
    # 1. Create a user with plaintext fields
    user = SecureUser.create(
        name="John Doe",
        ssn="123-45-6789",
        api_key="sk_live_abcdef123456",
    )

    # In-memory attributes must be plaintext
    assert user.name == "John Doe"
    assert user.ssn == "123-45-6789"
    assert user.api_key == "sk_live_abcdef123456"

    # 2. Inspect the raw values written to the database using raw SQL
    engine = SecureUser.get_engine()
    rows = engine.execute(
        f"SELECT name, ssn, api_key FROM {SecureUser._table} WHERE id = ?", (user.id,)
    )
    assert len(rows) == 1
    raw_row = rows[0]

    # Name is stored as plaintext, but ssn and api_key must be encrypted ciphertexts
    assert raw_row["name"] == "John Doe"
    assert raw_row["ssn"] != "123-45-6789"
    assert raw_row["ssn"].startswith("gAAAAA")  # Fernet tokens start with gAAAAA
    assert raw_row["api_key"] != "sk_live_abcdef123456"
    assert raw_row["api_key"].startswith("gAAAAA")

    # 3. Reload from DB and verify automatic decryption
    loaded_user = SecureUser.find(id=user.id)
    assert loaded_user.name == "John Doe"
    assert loaded_user.ssn == "123-45-6789"
    assert loaded_user.api_key == "sk_live_abcdef123456"

    # Verify to_dict() returns plaintext
    data = loaded_user.to_dict()
    assert data["name"] == "John Doe"
    assert data["ssn"] == "123-45-6789"
    assert data["api_key"] == "sk_live_abcdef123456"


def test_encrypted_field_key_mismatch(monkeypatch):
    # 1. Create a user with initial key
    monkeypatch.setenv("SECRET_KEY", "key-number-one-secret-key-number-one")
    user = SecureUser.create(
        name="Alice",
        ssn="987-65-4321",
    )

    # 2. Verify we can decrypt with the same key
    loaded = SecureUser.find(id=user.id)
    assert loaded.ssn == "987-65-4321"

    # 3. Change SECRET_KEY to simulate key rotation/mismatch
    monkeypatch.setenv("SECRET_KEY", "key-number-two-secret-key-number-two")

    # Decryption should gracefully fall back to returning the raw database ciphertext (starting with gAAAAA)
    # instead of crashing the query or instance loading.
    loaded_mismatch = SecureUser.find(id=user.id)
    assert loaded_mismatch.ssn != "987-65-4321"
    assert loaded_mismatch.ssn.startswith("gAAAAA")


def test_encrypted_field_null_handling():
    # 1. Create a user with a NULL encrypted field
    user = SecureUser.create(
        name="Bob",
        ssn=None,
    )

    # Null value should remain None
    assert user.ssn is None

    # Load from DB and verify it is still None (and not converted or encrypted)
    loaded = SecureUser.find(id=user.id)
    assert loaded.ssn is None


def test_encrypted_field_missing_secret_key(monkeypatch):
    # Remove SECRET_KEY from environment
    monkeypatch.delenv("SECRET_KEY", raising=False)
    # Clear the encryption key cache to force key resolution
    from asok.orm.model import _ENCRYPTION_KEY_CACHE

    _ENCRYPTION_KEY_CACHE.clear()

    # Attempting to encrypt a value without SECRET_KEY must raise RuntimeError
    user = SecureUser(name="Bob", ssn="123-45-6789")
    with pytest.raises(RuntimeError, match="SECRET_KEY is not configured"):
        user.save()
