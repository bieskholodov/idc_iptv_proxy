from datetime import timedelta
import logging
import os
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_INTERVAL,
    CONF_TEMPLATE_PATH,
    CONF_OUTPUT_PATH,
    DEFAULT_HOST,
    DEFAULT_INTERVAL,
    DEFAULT_TEMPLATE_PATH,
    DEFAULT_OUTPUT_PATH,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    login = entry.data[CONF_LOGIN]
    password = entry.data[CONF_PASSWORD]
    interval = entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)
    template_path = entry.data.get(CONF_TEMPLATE_PATH, DEFAULT_TEMPLATE_PATH)
    output_path = entry.data.get(CONF_OUTPUT_PATH, DEFAULT_OUTPUT_PATH)

    session = async_get_clientsession(hass)

    async def _async_update_data():
        url = f"https://{host}/api/json/login"
        params = {
            "login": login,
            "pass": password,
            "settings": "all",
        }

        try:
            async with session.get(url, params=params, timeout=15) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as err:
            raise UpdateFailed(f"Ошибка запроса к IDC API: {err}") from err

        # 1. Извлекаем актуальный токен сессии (проверьте точный ключ в JSON от IDC)
        token = data.get("ss") or data.get("sid") or data.get("token")
        
        # 2. Извлекаем актуальный стриминговый домен (например, iptv-live.idc.md или другой из settings)
        # Если API возвращает полный URL или домен в другом поле, поправим ключ
        stream_domain = (
            data.get("stream_domain")
            or data.get("streamer")
            or data.get("hls_domain")
            or "iptv-live.idc.md"
        )
        # Очищаем домен от https:// и слешей, если API вернул его в виде URL
        stream_domain = re.sub(r"^https?://", "", str(stream_domain)).strip("/")

        if not token:
            _LOGGER.warning("Токен не найден в ответе API. Доступные ключи: %s", list(data.keys()))
            return data

        await hass.async_add_executor_job(
            _update_playlist_file, template_path, output_path, stream_domain, token
        )
        return data

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=_async_update_data,
        update_interval=timedelta(seconds=interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return True


def _update_playlist_file(
    template_path: str, output_path: str, new_domain: str, new_token: str
) -> None:
    if not os.path.exists(template_path):
        _LOGGER.error(
            "Файл шаблона плейлиста не найден по пути: %s. Положите туда исходный .m3u файл.",
            template_path,
        )
        return

    with open(template_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    updated_lines = []
    for line in lines:
        stripped = line.strip()
        # Меняем домен и токен только в строках со ссылками на потоки (не трогаем #EXTINF и логотипы)
        if stripped.startswith("http"):
            # Заменяем домен после https:// или http://
            line = re.sub(r"^(https?://)[^/]+", rf"\g<1>{new_domain}", line)
            # Заменяем значение параметра ss=...
            if "ss=" in line:
                line = re.sub(r"([?&]ss=)[^&\s]+", rf"\g<1>{new_token}", line)
            else:
                separator = "&" if "?" in line else "?"
                line = f"{stripped}{separator}ss={new_token}\n"
        updated_lines.append(line)

    with open(output_path, "w", encoding="utf-8") as file:
        file.writelines(updated_lines)

    _LOGGER.debug(
        "Плейлист обновлен: домен=%s, токен=%s*** -> %s",
        new_domain,
        new_token[:6],
        output_path,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
