import pytest

from fintrack.crypto import KeyMissingError, TokenCipher, ensure_key, has_key


def test_roundtrip_and_ciphertext_hides_token(home):
    assert ensure_key(home) is True
    cipher = TokenCipher(home)
    token = "access-sandbox-1234abcd-0000-0000-0000-000000000000"
    blob = cipher.encrypt(token)
    assert token.encode() not in blob
    assert cipher.decrypt(blob) == token


def test_ensure_key_is_idempotent(home):
    ensure_key(home)
    blob = TokenCipher(home).encrypt("secret")
    assert ensure_key(home) is False  # existing key kept
    assert TokenCipher(home).decrypt(blob) == "secret"


def test_missing_key_is_an_error_not_a_new_key(home):
    assert not has_key(home)
    with pytest.raises(KeyMissingError):
        TokenCipher(home)
    assert not has_key(home)


def test_keys_are_scoped_per_data_folder(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    ensure_key(a)
    ensure_key(b)
    blob = TokenCipher(a).encrypt("secret")
    with pytest.raises(KeyMissingError):
        TokenCipher(b).decrypt(blob)
