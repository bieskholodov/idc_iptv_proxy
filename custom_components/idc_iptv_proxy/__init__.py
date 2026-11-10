
from datetime import timedelta
import logging

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
    CONF_OUTPUT_PATH,
    DEFAULT_HOST,
    DEFAULT_INTERVAL,
    DEFAULT_OUTPUT_PATH,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    login = entry.data[CONF_LOGIN]
    password = entry.data[CONF_PASSWORD]
    interval = entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)
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

        # Извлекаем токен сессии (проверим точное имя поля по вашему JSON)
        token = data.get("ss") or data.get("sid") or data.get("token")
        if not token:
            _LOGGER.warning("Токен не найден в ответе API: %s", list(data.keys()))
            return data

        m3u_content = _build_m3u(data, token)

        # Записываем файл в отдельном потоке, чтобы не блокировать цикл событий HA
        await hass.async_add_executor_job(_write_file, output_path, m3u_content)
        _LOGGER.debug("Плейлист IDC IPTV обновлен: %s", output_path)
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


def _build_m3u(data: dict, token: str) -> str:
    lines = ["#EXTM3U"]
    # Пример для канала ТСВ из вашего шаблона
    lines.append(
        '#EXTINF:-1 tvg-id="2" tvg-logo="https://idc.md" group-title="Общий",ТСВ'
    )
    lines.append(f"https://idc.md{token}")
    return "\n".join(lines) + "\n"


def _write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as file:
        file.write(content)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id, None)
    return True
