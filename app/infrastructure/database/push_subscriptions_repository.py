"""Encrypted Web Push subscription persistence and delivery queue."""

import base64
import binascii
from datetime import UTC, datetime
import hashlib
import json
import secrets
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.infrastructure.database.connection import transaction


_NONCE_BYTES = 12
_MAX_ENDPOINT_LENGTH = 2048


def decode_subscription_encryption_key(encoded: str) -> bytes:
    """Decode the dedicated base64url 256-bit subscription-at-rest key."""
    value = encoded.strip()
    if not value:
        raise ValueError("Web Push subscription encryption key is required")
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        key = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ValueError("Web Push subscription key must be base64url encoded") from error
    if len(key) != 32:
        raise ValueError("Web Push subscription key must decode to exactly 32 bytes")
    return key


def _decode_browser_key(value: str, *, expected_length: int, name: str) -> bytes:
    try:
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        decoded = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as error:
        raise ValueError(f"Invalid Web Push {name} key") from error
    if len(decoded) != expected_length:
        raise ValueError(f"Invalid Web Push {name} key length")
    return decoded


class PushSubscriptionsRepository:
    """Store browser push endpoints and keys only as authenticated ciphertext."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def upsert_subscription(
        self,
        *,
        user_id: str,
        endpoint: str,
        p256dh: str,
        auth: str,
        encryption_key: bytes,
        now: datetime,
    ) -> str:
        """Create or refresh one browser subscription for the authenticated user."""
        self._require_aware(now)
        self._validate_key(encryption_key)
        parsed = urlsplit(endpoint)
        if (
            not endpoint
            or len(endpoint) > _MAX_ENDPOINT_LENGTH
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("Web Push endpoint must be a valid HTTPS URL")
        _decode_browser_key(p256dh, expected_length=65, name="p256dh")
        _decode_browser_key(auth, expected_length=16, name="auth")
        endpoint_hash = hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
        subscription = json.dumps(
            {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            separators=(",", ":"),
        ).encode("utf-8")
        nonce = secrets.token_bytes(_NONCE_BYTES)
        encrypted = nonce + AESGCM(encryption_key).encrypt(
            nonce, subscription, user_id.encode("utf-8")
        )
        subscription_id = str(uuid4())
        timestamp = self._format(now)
        with transaction(self._connection):
            existing = self._connection.execute(
                """SELECT id, user_id FROM push_subscriptions
                   WHERE endpoint_hash = ?""",
                (endpoint_hash,),
            ).fetchone()
            if existing is not None and str(existing["user_id"]) != user_id:
                raise ValueError("This browser subscription belongs to another account")
            if existing is None:
                self._connection.execute(
                    """INSERT INTO push_subscriptions(
                        id, user_id, endpoint_hash, subscription_ciphertext,
                        created_at, updated_at, revoked_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL)""",
                    (
                        subscription_id,
                        user_id,
                        endpoint_hash,
                        encrypted,
                        timestamp,
                        timestamp,
                    ),
                )
                return subscription_id
            subscription_id = str(existing["id"])
            self._connection.execute(
                """UPDATE push_subscriptions
                   SET subscription_ciphertext = ?, updated_at = ?, revoked_at = NULL
                   WHERE id = ?""",
                (encrypted, timestamp, subscription_id),
            )
        return subscription_id

    def has_active_subscription(self, *, user_id: str) -> bool:
        row = self._connection.execute(
            """SELECT 1 FROM push_subscriptions
               WHERE user_id = ? AND revoked_at IS NULL LIMIT 1""",
            (user_id,),
        ).fetchone()
        return row is not None

    def delete_user_subscriptions(self, *, user_id: str) -> int:
        """Delete encrypted browser endpoints and their delivery history."""
        with transaction(self._connection):
            cursor = self._connection.execute(
                "DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,)
            )
        return cursor.rowcount

    def pending_deliveries(
        self,
        *,
        encryption_key: bytes,
        now: datetime,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """Materialize and return pending deliveries for opted-in subscriptions."""
        self._require_aware(now)
        self._validate_key(encryption_key)
        if limit < 1:
            raise ValueError("Push delivery batch size must be positive")
        timestamp = self._format(now)
        with transaction(self._connection):
            candidates = self._connection.execute(
                """SELECT notification.id AS notification_id,
                          subscription.id AS subscription_id
                   FROM notifications AS notification
                   JOIN notification_preferences AS preference
                     ON preference.user_id = notification.user_id
                    AND preference.push_enabled = 1
                    AND preference.push_consent_at IS NOT NULL
                   JOIN push_subscriptions AS subscription
                     ON subscription.user_id = notification.user_id
                    AND subscription.revoked_at IS NULL
                   WHERE notification.created_at >= preference.push_consent_at
                     AND notification.created_at >= subscription.created_at
                   ORDER BY notification.created_at, notification.id""",
            ).fetchall()
            for candidate in candidates:
                self._connection.execute(
                    """INSERT OR IGNORE INTO push_deliveries(
                        id, notification_id, subscription_id, attempts,
                        sent_at, last_error, created_at, updated_at
                    ) VALUES (?, ?, ?, 0, NULL, NULL, ?, ?)""",
                    (
                        str(uuid4()),
                        candidate["notification_id"],
                        candidate["subscription_id"],
                        timestamp,
                        timestamp,
                    ),
                )
        rows = self._connection.execute(
            """SELECT delivery.id AS delivery_id, delivery.subscription_id,
                      notification.id AS notification_id, notification.title,
                      notification.body, subscription.user_id,
                      subscription.subscription_ciphertext
               FROM push_deliveries AS delivery
               JOIN notifications AS notification
                 ON notification.id = delivery.notification_id
               JOIN push_subscriptions AS subscription
                 ON subscription.id = delivery.subscription_id
               JOIN notification_preferences AS preference
                 ON preference.user_id = subscription.user_id
                AND preference.push_enabled = 1
               WHERE delivery.sent_at IS NULL
                 AND delivery.attempts < 5
                 AND subscription.revoked_at IS NULL
               ORDER BY delivery.created_at, delivery.id LIMIT ?""",
            (limit,),
        ).fetchall()
        pending: list[dict[str, object]] = []
        invalid_subscriptions: set[str] = set()
        for row in rows:
            subscription_id = str(row["subscription_id"])
            user_id = str(row["user_id"])
            encrypted = bytes(row["subscription_ciphertext"])
            if len(encrypted) <= _NONCE_BYTES:
                invalid_subscriptions.add(subscription_id)
                continue
            try:
                plaintext = AESGCM(encryption_key).decrypt(
                    encrypted[:_NONCE_BYTES],
                    encrypted[_NONCE_BYTES:],
                    user_id.encode("utf-8"),
                )
                subscription_info = json.loads(plaintext)
            except (InvalidTag, ValueError, json.JSONDecodeError):
                invalid_subscriptions.add(subscription_id)
                continue
            pending.append(
                {
                    "delivery_id": str(row["delivery_id"]),
                    "notification_id": str(row["notification_id"]),
                    "subscription_id": subscription_id,
                    "title": str(row["title"]),
                    "body": str(row["body"]),
                    "subscription_info": subscription_info,
                }
            )
        for subscription_id in invalid_subscriptions:
            self.delete_subscription(subscription_id=subscription_id, now=now)
        return pending

    def record_failure(
        self, *, delivery_id: str, error_name: str, now: datetime
    ) -> None:
        self._require_aware(now)
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE push_deliveries
                   SET attempts = attempts + 1, last_error = ?, updated_at = ?
                   WHERE id = ? AND sent_at IS NULL""",
                (error_name[:120], self._format(now), delivery_id),
            )

    def record_sent(self, *, delivery_id: str, now: datetime) -> None:
        self._require_aware(now)
        with transaction(self._connection):
            self._connection.execute(
                """UPDATE push_deliveries
                   SET attempts = attempts + 1, sent_at = ?, last_error = NULL,
                       updated_at = ?
                   WHERE id = ? AND sent_at IS NULL""",
                (self._format(now), self._format(now), delivery_id),
            )

    def delete_subscription(self, *, subscription_id: str, now: datetime) -> bool:
        self._require_aware(now)
        with transaction(self._connection):
            row = self._connection.execute(
                "SELECT user_id FROM push_subscriptions WHERE id = ?",
                (subscription_id,),
            ).fetchone()
            if row is None:
                return False
            user_id = str(row["user_id"])
            cursor = self._connection.execute(
                "DELETE FROM push_subscriptions WHERE id = ?", (subscription_id,)
            )
            remaining = self._connection.execute(
                """SELECT 1 FROM push_subscriptions
                   WHERE user_id = ? AND revoked_at IS NULL LIMIT 1""",
                (user_id,),
            ).fetchone()
            if remaining is None:
                self._connection.execute(
                    """UPDATE notification_preferences
                       SET push_enabled = 0, push_consent_at = NULL,
                           push_consent_version = NULL, updated_at = ?
                       WHERE user_id = ?""",
                    (self._format(now), user_id),
                )
        return cursor.rowcount == 1

    @staticmethod
    def _validate_key(key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Web Push subscription encryption key must be 32 bytes")

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Datetime must include a timezone")

    @classmethod
    def _format(cls, value: datetime) -> str:
        cls._require_aware(value)
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
            "+00:00", "Z"
        )
