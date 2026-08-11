"""Thin adapter over Hermes automation blueprints and delivery discovery."""

from __future__ import annotations

from typing import Any, Mapping


class CronDeliveryAdapterError(RuntimeError):
    pass


class CronBlueprintNotFound(CronDeliveryAdapterError):
    pass


class CronBlueprintInvalid(CronDeliveryAdapterError):
    pass


class CronDeliveryAdapter:
    def list_blueprints(self) -> list[dict[str, Any]]:
        try:
            from cron.blueprint_catalog import CATALOG, blueprint_catalog_entry
        except Exception as exc:
            raise CronDeliveryAdapterError("Hermes automation blueprints are unavailable") from exc
        rows: list[dict[str, Any]] = []
        for blueprint in CATALOG:
            entry = dict(blueprint_catalog_entry(blueprint))
            entry["schedule_human"] = entry.pop("scheduleHuman", "")
            entry["app_url"] = entry.pop("appUrl", "")
            rows.append(entry)
        return rows

    def fill(self, blueprint_key: str, values: Mapping[str, Any]) -> dict[str, Any]:
        try:
            from cron.blueprint_catalog import BlueprintFillError, fill_blueprint, get_blueprint
        except Exception as exc:
            raise CronDeliveryAdapterError("Hermes automation blueprints are unavailable") from exc
        blueprint = get_blueprint(str(blueprint_key or "").strip())
        if blueprint is None:
            raise CronBlueprintNotFound("automation blueprint not found")
        try:
            return dict(fill_blueprint(blueprint, dict(values)))
        except BlueprintFillError as exc:
            raise CronBlueprintInvalid(str(exc)) from exc


__all__ = [
    "CronBlueprintInvalid",
    "CronBlueprintNotFound",
    "CronDeliveryAdapter",
    "CronDeliveryAdapterError",
]
