"""Notification persistence."""

from __future__ import annotations

from typing import Any, Mapping

from .base import StoreError


class NotificationRepositoryMixin:
    """Notification listing and mutation operations."""

    def list_notifications(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.notifications_root.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(item, dict):
                    result.append(item)
            except (OSError, json.JSONDecodeError):
                continue
        return result

    def put_notification(self, item: Mapping[str, Any]) -> dict[str, Any]:
        notification = dict(item)
        path = (
            self.notifications_root / f"{self._id(notification.get('id'), 'notification id')}.json"
        )
        self.atomic_json(path, notification)
        return notification

    def resolve_notification(self, notification_id: Any) -> dict[str, Any]:
        path = self.notifications_root / f"{self._id(notification_id, 'notification id')}.json"
        if not path.is_file():
            raise StoreError("notification not found", status=404, code="not_found")
        try:
            notification = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoreError(
                "notification is invalid", status=500, code="invalid_notification"
            ) from error
        notification["resolved"] = True
        notification["resolved_at"] = time.time()
        self.atomic_json(path, notification)
        return notification
