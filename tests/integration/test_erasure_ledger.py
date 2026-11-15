from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess

import pytest

from app.infrastructure.database.erasure_ledger import (
    AccountErasureLedger,
    ErasureLedgerError,
)


def test_ledger_stores_signed_pseudonymous_tombstone(tmp_path) -> None:
    ledger = AccountErasureLedger(tmp_path / "ledger", b"k" * 32)

    event = ledger.record_deletion(
        user_id="private-account-id",
        deleted_at=datetime(2026, 11, 15, tzinfo=UTC),
    )

    files = list((tmp_path / "ledger").glob("*.json"))
    assert len(files) == 1
    document = json.loads(files[0].read_text(encoding="utf-8"))
    assert document["event_id"] == event.event_id
    assert document["user_id_hash"] == event.user_id_hash
    assert "private-account-id" not in files[0].read_text(encoding="utf-8")
    assert files[0].stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "ledger").stat().st_mode & 0o777 == 0o700
    assert ledger.load_events() == (event,)


def test_ledger_rejects_modified_or_unsigned_tombstone(tmp_path) -> None:
    ledger = AccountErasureLedger(tmp_path / "ledger", b"k" * 32)
    ledger.record_deletion(
        user_id="private-account-id", deleted_at=datetime.now(UTC)
    )
    event_file = next((tmp_path / "ledger").glob("*.json"))
    document = json.loads(event_file.read_text(encoding="utf-8"))
    document["user_id_hash"] = "0" * 64
    event_file.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ErasureLedgerError, match="failed verification"):
        ledger.load_events()


def test_ledger_fails_closed_when_remote_replication_fails(tmp_path, monkeypatch) -> None:
    ledger = AccountErasureLedger(
        tmp_path / "ledger",
        b"k" * 32,
        remote="backup:erasure-ledger",
    )

    def fail_remote(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "rclone")

    monkeypatch.setattr(
        "app.infrastructure.database.erasure_ledger.subprocess.run",
        fail_remote,
    )
    with pytest.raises(ErasureLedgerError, match="off-host"):
        ledger.record_deletion(
            user_id="private-account-id", deleted_at=datetime.now(UTC)
        )
    # The local intent remains durable so a later retry/startup can replicate it.
    assert len(list((tmp_path / "ledger").glob("*.json"))) == 1



def test_remote_ledger_can_recover_tombstones_after_local_copy_is_lost(
    tmp_path, monkeypatch
) -> None:
    local_directory = tmp_path / "local-ledger"
    remote_directory = tmp_path / "remote-ledger"
    remote_directory.mkdir()
    ledger = AccountErasureLedger(
        local_directory,
        b"k" * 32,
        remote="mock:account-erasure",
    )

    def fake_rclone(arguments, **kwargs):
        if arguments[3] == "mock:account-erasure":
            source = Path(arguments[2])
            for source_file in source.glob("*.json"):
                target = remote_directory / source_file.name
                if target.exists() and target.read_bytes() != source_file.read_bytes():
                    raise subprocess.CalledProcessError(1, arguments)
                target.write_bytes(source_file.read_bytes())
        elif arguments[2] == "mock:account-erasure":
            destination = Path(arguments[3])
            for source_file in remote_directory.glob("*.json"):
                (destination / source_file.name).write_bytes(source_file.read_bytes())
        return subprocess.CompletedProcess(arguments, 0)

    monkeypatch.setattr(
        "app.infrastructure.database.erasure_ledger.subprocess.run",
        fake_rclone,
    )
    event = ledger.record_deletion(
        user_id="deleted-account", deleted_at=datetime.now(UTC)
    )
    next(local_directory.glob("*.json")).unlink()

    assert ledger.load_events() == (event,)

def test_matching_tombstones_only_select_deleted_account_ids(tmp_path) -> None:
    ledger = AccountErasureLedger(tmp_path / "ledger", b"k" * 32)
    event = ledger.record_deletion(
        user_id="old-user", deleted_at=datetime.now(UTC)
    )

    assert ledger.matching_user_ids(
        ["new-user", "old-user"], (event,)
    ) == ("old-user",)
