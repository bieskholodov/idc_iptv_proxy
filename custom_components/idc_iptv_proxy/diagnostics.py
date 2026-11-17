"""Диагностика интеграции IDC IPTV.

Позволяет скачать диагностические данные со страницы интеграции
(Настройки -> Устройства и службы -> IDC IPTV -> три точки ->
"Скачать диагностику").
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_INTERVAL,
    CONF_OUTPUT_PATH,
    CONF_TEMPLATE_PATH,
    DOMAIN,
)

# Ключи, которые обязательно скрываем перед выгрузкой диагностики
_SECRET_KEYS = {
    "login",
    "pass",
    "password",
    "token",
    "sid",
    "ssid",
    "ss",
    "ticket",
    "cookie",
}


def _redact(value: Any) -> Any:
    """Рекурсивно скрываем секреты в словарях и списках."""
    if isinstance(value, dict):
        return {
            key: ("<скрыто>" if str(key).lower() in _SECRET_KEYS else _redact(val))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _mask_token(token: str | None, visible: int = 6) -> str | None:
    """Показываем только начало токена, чтобы помочь отладке, но не утечь."""
    if not token:
        return None
    return f"{token[:visible]}*** ({len(token)} симв.)"


def _playlist_file_info(output_path: str | None) -> dict[str, Any] | None:
    """Краткая информация о сгенерированном плейлисте (синхронная часть)."""
    if not output_path:
        return None
    try:
        stat = os.stat(output_path)
        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    except FileNotFoundError:
        return {"exists": False}
    except OSError as err:
        return {"exists": None, "error": repr(err)}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Возвращаем диагностику для конкретной записи конфигурации."""
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = store.get("coordinator")
    session = store.get("session")

    coordinator_data = getattr(coordinator, "data", None) or {}

    return {
        "integration": DOMAIN,
        "entry": {
            "title": entry.title,
            "entry_id": entry.entry_id,
            "state": entry.state.value,
            "data": _redact(dict(entry.data)),
            "options": _redact(dict(entry.options)),
        },
        "coordinator": {
            "last_update_success": getattr(coordinator, "last_update_success", None),
            "update_interval": str(getattr(coordinator, "update_interval", "")),
            "last_exception": repr(getattr(coordinator, "last_exception", None))
            if getattr(coordinator, "last_exception", None)
            else None,
            "data": _redact(coordinator_data) if coordinator_data else None,
        },
        "session_state": {
            "stream_domain": coordinator_data.get("domain"),
            "ss_token": _mask_token(coordinator_data.get("ss")),
            "aiohttp_session_alive": bool(session and not session.closed),
        },
        "playlist": {
            "template_path": entry.data.get(CONF_TEMPLATE_PATH),
            "output_path": entry.data.get(CONF_OUTPUT_PATH),
            "output_file": await hass.async_add_executor_job(
                _playlist_file_info, entry.data.get(CONF_OUTPUT_PATH)
            ),
        },
        "host": entry.data.get(CONF_HOST),
        "interval_sec": entry.data.get(CONF_INTERVAL),
        "ha": {"version": hass.config.version},
    }
