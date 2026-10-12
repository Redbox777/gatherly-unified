"""Secure token primitives for authentication and account-recovery flows."""

from dataclasses import dataclass
import hashlib
import secrets


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A raw one-time token and its database-safe digest.

    Persist only the digest. The raw value is returned once to the caller and
    must never be logged or stored in the database.
    """

    raw: str
    digest: str


def issue_token(*, byte_length: int = 32) -> IssuedToken:
    """Create a cryptographically random URL-safe token and its SHA-256 digest."""
    if byte_length < 32:
        raise ValueError("Token entropy must be at least 32 bytes")
    raw = secrets.token_urlsafe(byte_length)
    digest = hash_token(raw)
    return IssuedToken(raw=raw, digest=digest)


def hash_token(raw: str) -> str:
    """Return a stable SHA-256 digest suitable for high-entropy token lookup."""
    if not raw or not raw.strip():
        raise ValueError("Token must not be empty")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
