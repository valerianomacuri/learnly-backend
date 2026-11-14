"""Password hashing (Argon2id through pwdlib).

Only the hash is ever stored; plaintext passwords are never logged or returned.
"""

from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher

_hasher = PasswordHash((Argon2Hasher(),))

# Stored by the migration for users that predate passwords: it matches no hash
# format, so those users cannot log in until a password is set.
UNUSABLE_HASH = "!"


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


# Verified against when the email is unknown, so both failures cost the same.
DUMMY_HASH = hash_password("dummy-password-for-timing")


def verify_password(plain: str, hashed: str) -> tuple[bool, str | None]:
    """Return (valid, new_hash). `new_hash` is set when the hash should be upgraded."""
    try:
        return _hasher.verify_and_update(plain, hashed)
    except UnknownHashError:
        return False, None
