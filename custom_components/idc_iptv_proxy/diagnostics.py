from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    CONF_PASSWORD,
    CONF_LOGIN,
    CONF_TEMPLATE_PATH,
    CONF_OUTPUT_PATH,
    DEFAULT_TEMPLATE_PATH,
    DEFAULT_OUTPUT_PATH,
)

# Поля, которые будут заменены на **REDACTED** в итоговом JSON-отчете
TO_REDACT = {CONF_PASSWORD, CONF_LOGIN, "ss", "ssid", "token", "ticket"}


def _get_file_info(path: str) -> dict[str, Any]:
    """Проверка существования, размера и времени изменения файла."""
    if not os.path.exists(path):
        return {"exists": False, "path": path}

    stat = os.stat(path)
    with open(path, "r", encoding="utf-8", errors="ignore") as file:
        lines_count = sum(1 for _ in file)

    return {
        "exists": True,
        "path": path,
        "size_bytes": stat.st_size,
        "lines_count": lines_count,
        "last_modified_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Сбор диагностических данных для интеграции IDC IPTV."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = entry_data["coordinator"]
    state = coordinator.data or {}

    template_path = entry.data.get(CONF_TEMPLATE_PATH, DEFAULT_TEMPLATE_PATH)
    output_path = entry.data.get(CONF_OUTPUT_PATH, DEFAULT_OUTPUT_PATH)

    template_info = await hass.async_add_executor_job(_get_file_info, template_path)
    output_info = await hass.async_add_executor_job(_get_file_info, output_path)

    raw_ss = state.get("ss")
    last_exc = getattr(coordinator, "last_exception", None)

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "version": entry.version,
            "domain": entry.domain,
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": str(last_exc) if last_exc else None,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "session_state": {
            "domain": state.get("domain"),
            "has_active_token": bool(raw_ss),
            "ss_preview": f"{raw_ss[:6]}***" if raw_ss else None,
        },
        "files": {
            "template_file": template_info,
            "output_playlist": output_info,
        },
    }

