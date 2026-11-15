"""Versioned password hashing with Argon2id and legacy scrypt verification."""

import base64
import binascii
import hashlib
import hmac

from argon2 import PasswordHasher, Type, extract_parameters
from argon2.exceptions import InvalidHashError, VerificationError


_ARGON2_MEMORY_COST_KIB = 19_456
_ARGON2_TIME_COST = 2
_ARGON2_PARALLELISM = 1
_ARGON2_SALT_BYTES = 16
_ARGON2_HASH_BYTES = 32
_ARGON2 = PasswordHasher(
    time_cost=_ARGON2_TIME_COST,
    memory_cost=_ARGON2_MEMORY_COST_KIB,
    parallelism=_ARGON2_PARALLELISM,
    hash_len=_ARGON2_HASH_BYTES,
    salt_len=_ARGON2_SALT_BYTES,
    type=Type.ID,
)

_LEGACY_SCRYPT_ALGORITHM = "scrypt"
_LEGACY_SCRYPT_N = 2**14
_LEGACY_SCRYPT_R = 8
_LEGACY_SCRYPT_P = 1
_LEGACY_SCRYPT_SALT_BYTES = 16
_LEGACY_SCRYPT_KEY_BYTES = 32
_MAX_PASSWORD_BYTES = 1024


def hash_password(password: str) -> str:
    """Hash a password with Argon2id using the current bounded parameters."""
    _password_bytes(password)
    return _ARGON2.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify current Argon2id hashes and legacy scrypt hashes."""
    try:
        password_bytes = _password_bytes(password)
        if encoded_hash.startswith("$argon2id$"):
            parameters = extract_parameters(encoded_hash)
            if (
                parameters.type is not Type.ID
                or parameters.version != 19
                or parameters.memory_cost != _ARGON2_MEMORY_COST_KIB
                or parameters.time_cost != _ARGON2_TIME_COST
                or parameters.parallelism != _ARGON2_PARALLELISM
                or parameters.salt_len != _ARGON2_SALT_BYTES
                or parameters.hash_len != _ARGON2_HASH_BYTES
            ):
                return False
            return _ARGON2.verify(encoded_hash, password)
        return _verify_legacy_scrypt(password_bytes, encoded_hash)
    except (
        ValueError,
        TypeError,
        UnicodeEncodeError,
        binascii.Error,
        VerificationError,
        InvalidHashError,
    ):
        return False


def password_hash_needs_rehash(encoded_hash: str) -> bool:
    """Identify successfully verified legacy hashes that should be upgraded."""
    if encoded_hash.startswith(_LEGACY_SCRYPT_ALGORITHM + "$"):
        return True
    if encoded_hash.startswith("$argon2id$"):
        try:
            return _ARGON2.check_needs_rehash(encoded_hash)
        except (InvalidHashError, VerificationError):
            return False
    return False


def _verify_legacy_scrypt(password_bytes: bytes, encoded_hash: str) -> bool:
    algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded_hash.split("$")
    if (
        algorithm != _LEGACY_SCRYPT_ALGORITHM
        or (int(n_text), int(r_text), int(p_text))
        != (_LEGACY_SCRYPT_N, _LEGACY_SCRYPT_R, _LEGACY_SCRYPT_P)
    ):
        return False
    salt = base64.b64decode(salt_text.encode("ascii"), altchars=b"-_", validate=True)
    expected = base64.b64decode(
        digest_text.encode("ascii"), altchars=b"-_", validate=True
    )
    if len(salt) != _LEGACY_SCRYPT_SALT_BYTES or len(expected) != _LEGACY_SCRYPT_KEY_BYTES:
        return False
    actual = hashlib.scrypt(
        password_bytes,
        salt=salt,
        n=_LEGACY_SCRYPT_N,
        r=_LEGACY_SCRYPT_R,
        p=_LEGACY_SCRYPT_P,
        dklen=_LEGACY_SCRYPT_KEY_BYTES,
    )
    return hmac.compare_digest(actual, expected)


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str) or not password:
        raise ValueError("Password must not be empty")
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError("Password is too long")
    return encoded
