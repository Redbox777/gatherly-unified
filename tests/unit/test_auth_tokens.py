import re

import pytest

from app.domain.auth_tokens import hash_token, issue_token


def test_issued_token_is_random_and_only_digest_is_stable() -> None:
    first = issue_token()
    second = issue_token()

    assert first.raw != second.raw
    assert first.digest == hash_token(first.raw)
    assert first.digest != second.digest
    assert re.fullmatch(r"[0-9a-f]{64}", first.digest)


def test_token_generation_rejects_weak_entropy() -> None:
    with pytest.raises(ValueError, match="at least 32 bytes"):
        issue_token(byte_length=16)


@pytest.mark.parametrize("raw", ["", " ", "\n"])
def test_hash_token_rejects_empty_values(raw: str) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        hash_token(raw)
