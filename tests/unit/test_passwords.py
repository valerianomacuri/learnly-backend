from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

from app.core.passwords import (
    DUMMY_HASH,
    UNUSABLE_HASH,
    hash_password,
    verify_password,
)


def test_hash_is_argon2id_and_not_the_plaintext() -> None:
    hashed = hash_password("correct horse")
    assert hashed.startswith("$argon2id$")
    assert "correct horse" not in hashed


def test_hashes_are_salted() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_accepts_the_right_password_only() -> None:
    hashed = hash_password("correct horse")
    assert verify_password("correct horse", hashed)[0] is True
    assert verify_password("Correct horse", hashed)[0] is False
    assert verify_password("", hashed)[0] is False


def test_unusable_and_garbage_hashes_never_verify_and_never_raise() -> None:
    for bad in (UNUSABLE_HASH, "", "plaintext", "$unknown$x$y"):
        assert verify_password("anything", bad) == (False, None)
        assert verify_password(bad, bad) == (False, None)


def test_weaker_parameters_are_upgraded_on_verify() -> None:
    weak = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),))
    old = weak.hash("correct horse")
    valid, new_hash = verify_password("correct horse", old)
    assert valid is True
    assert new_hash is not None and new_hash != old
    assert verify_password("correct horse", new_hash) == (True, None)


def test_dummy_hash_never_matches() -> None:
    assert verify_password("anything", DUMMY_HASH)[0] is False
