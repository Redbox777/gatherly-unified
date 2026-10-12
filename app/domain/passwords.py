"""Password hashing primitives for local email accounts.

Passwords are never stored directly. The encoded format includes the algorithm
parameters and a random salt; verification accepts only the parameters this
module currently supports to avoid attacker-controlled resource costs.
"""

import base64
import binascii
import hashlib
import hmac
import secrets


_ALGORITHM = "scrypt"
_N = 2**14
_R = 8
_P = 1
_SALT_BYTES = 16
_KEY_BYTES = 32
_MAX_PASSWORD_BYTES = 1024


def hash_password(password: str) -> str:
    """Hash a password using scrypt and a fresh cryptographically random salt."""
    password_bytes = _password_bytes(password)
    salt = secrets.token_bytes(_SALT_BYTES)
    derived = hashlib.scrypt(
        password_bytes,
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_KEY_BYTES,
    )
    return "$".join(
        (
            _ALGORITHM,
            str(_N),
            str(_R),
            str(_P),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(derived).decode("ascii"),
        )
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify a password hash without exposing parsing or comparison details."""
    try:
        password_bytes = _password_bytes(password)
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded_hash.split("$")
        if (
            algorithm != _ALGORITHM
            or (int(n_text), int(r_text), int(p_text)) != (_N, _R, _P)
        ):
            return False
        salt = base64.b64decode(salt_text.encode("ascii"), altchars=b"-_", validate=True)
        expected = base64.b64decode(
            digest_text.encode("ascii"), altchars=b"-_", validate=True
        )
        if len(salt) != _SALT_BYTES or len(expected) != _KEY_BYTES:
            return False
        actual = hashlib.scrypt(
            password_bytes,
            salt=salt,
            n=_N,
            r=_R,
            p=_P,
            dklen=_KEY_BYTES,
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, UnicodeEncodeError, binascii.Error):
        return False


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str) or not password:
        raise ValueError("Password must not be empty")
    encoded = password.encode("utf-8")
    if len(encoded) > _MAX_PASSWORD_BYTES:
        raise ValueError("Password is too long")
    return encoded
