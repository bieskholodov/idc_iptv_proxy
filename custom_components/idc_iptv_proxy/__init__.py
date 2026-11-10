from datetime import timedelta
import logging
import os
import re
import socket

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    interval = min(entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL), 30)
    template_path = entry.data.get(CONF_TEMPLATE_PATH, DEFAULT_TEMPLATE_PATH)
    output_path = entry.data.get(CONF_OUTPUT_PATH, DEFAULT_OUTPUT_PATH)

    # Используем ThreadedResolver и только IPv4 (AF_INET), чтобы избежать таймаутов aiodns в HAOS
    connector = aiohttp.TCPConnector(
        resolver=aiohttp.ThreadedResolver(),
        family=socket.AF_INET,
        ssl=False,
    )
    cookie_jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(
        connector=connector,
        cookie_jar=cookie_jar,
        headers={
            "User-Agent": "Mozilla/5.0 (SmartHub; SMART-TV; U; Linux/SmartTV) AppleWebKit/537.42",
            "Referer": "https://iptv.idc.md/",
            "Origin": "https://iptv.idc.md",
            "X-Requested-With": "XMLHttpRequest",
        },
    )

    state = {
        "token": None,
        "sid_name": "SSID",
        "domain": "iptv-live.idc.md",
    }

    async def _logout() -> None:
        """Закрываем текущую сессию на сервере, чтобы освободить слот устройства."""
        try:
            logout_url = f"https://{host}/api/json/logout"
            params = {"ss": state["token"]} if state["token"] else {}
            headers = (
                {"Cookie": f"{state['sid_name']}={state['token']}"}
                if state["token"]
                else {}
            )
            async with session.get(logout_url, params=params, headers=headers, timeout=5):
                pass
        except Exception:
            pass
        finally:
            session.cookie_jar.clear()

    async def _fetch_stream_domain(token: str, sid_name: str) -> str:
        """Запрашиваем список каналов для удержания сессии и определения актуального домена стрима."""
        channels_url = f"https://{host}/api/json/get_channels"
        headers = {"Cookie": f"{sid_name}={token}"}
        try:
            async with session.get(
                channels_url,
                params={"ss": token},
                headers=headers,
                timeout=10,
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    domains = re.findall(
                        r"(iptv-[a-z0-9-]+\.idc\.md)", text, re.IGNORECASE
                    )
                    stream_domains = [
                        d for d in domains if not d.lower().startswith("iptvn.")
                    ]
                    if stream_domains:
                        return stream_domains[0]
        except Exception as err:
            _LOGGER.debug("Не удалось получить список каналов для поиска домена: %s", err)
        return state["domain"]

    async def _login_and_get_token() -> tuple[str | None, str, str]:
        await _logout()

        url = f"https://{host}/api/json/login"
        params = {
            "login": login,
            "pass": password,
            "settings": "all",
        }
        async with session.get(url, params=params, timeout=15) as resp:
            resp.raise_for_status()
            text = await resp.text()
            data = await resp.json(content_type=None)

        token = None
        sid_name = "SSID"
        if isinstance(data, dict):
            sid_name = data.get("sid_name") or "SSID"
            token = (
                data.get("sid")
                or data.get("ss")
                or data.get("token")
                or (
                    data.get("settings", {}).get("ss")
                    if isinstance(data.get("settings"), dict)
                    else None
                )
            )
        if not token:
            match = re.search(
                r'"(?:sid|ss|token)"\s*:\s*"([a-f0-9]{32})"', text, re.IGNORECASE
            )
            if match:
                token = match.group(1)

        domain = state["domain"]
        if token:
            domain = await _fetch_stream_domain(token, sid_name)

        return token, sid_name, domain

    async def _keep_session_alive(domain: str, token: str, sid_name: str) -> bool:
        """Продлеваем сессию на портале API и проверяем доступность HLS-потока."""
        headers = {"Cookie": f"{sid_name}={token}"}

        # 1. Пингуем API портала, чтобы сессия не закрывалась по неактивности
        try:
            async with session.get(
                f"https://{host}/api/json/get_channels",
                params={"ss": token},
                headers=headers,
                timeout=8,
            ) as api_resp:
                if api_resp.status == 200:
                    api_text = await api_resp.text()
                    if '"error"' in api_text.lower() or "auth" in api_text.lower():
                        _LOGGER.warning("API портала сообщил об истечении сессии: %s", api_text[:200])
                        return False
        except Exception as api_err:
            _LOGGER.debug("Ошибка пинга API портала: %s", api_err)

        # 2. Проверяем доступность потока (канал 2 - ТСВ)
        test_url = f"https://{domain}/hls/2/live1.m3u8?ss={token}"
        try:
            async with session.get(test_url, headers=headers, timeout=10) as resp:
                body = await resp.text()
                if (
                    resp.status == 200
                    and "#EXTM3U" in body
                    and ("#EXTINF" in body or ".ts" in body or "live" in body)
                ):
                    return True
                _LOGGER.warning(
                    "Детали отказа стрима %s: HTTP статус = %s, тело ответа = %s",
                    test_url,
                    resp.status,
                    body[:250],
                )
        except Exception as err:
            _LOGGER.warning("Ошибка соединения со стримом %s: %s", test_url, err)

        return False

    async def _async_update_data():
        try:
            current_token = state["token"]
            current_domain = state["domain"]
            current_sid_name = state["sid_name"]

            if current_token and await _keep_session_alive(
                current_domain, current_token, current_sid_name
            ):
                _LOGGER.debug(
                    "Сессия IDC активна, токен %s*** работает (%s)",
                    current_token[:6],
                    current_domain,
                )
                return state

            _LOGGER.info("Запрашиваю новую сессию IDC IPTV...")
            new_token, new_sid_name, new_domain = await _login_and_get_token()
            if not new_token:
                raise UpdateFailed("API IDC не вернул токен сессии (sid)")

            is_valid = await _keep_session_alive(new_domain, new_token, new_sid_name)
            if not is_valid:
                _LOGGER.warning(
                    "Стрим %s отклонил токен %s*** или недоступен. Повторю попытку в следующем цикле.",
                    new_domain,
                    new_token[:6],
                )
                return state

            state["token"] = new_token
            state["sid_name"] = new_sid_name
            state["domain"] = new_domain

            await hass.async_add_executor_job(
                _update_playlist_file,
                template_path,
                output_path,
                new_domain,
                new_token,
            )
            _LOGGER.info(
                "Сессия IDC IPTV активна: токен %s***, домен %s. Плейлист %s обновлен.",
                new_token[:6],
                new_domain,
                output_path,
            )
            return state

        except Exception as err:
            raise UpdateFailed(f"Ошибка поддержания сессии IDC IPTV: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=DOMAIN,
        update_method=_async_update_data,
        update_interval=timedelta(seconds=interval),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "session": session,
        "logout": _logout,
    }
    return True


def _update_playlist_file(
    template_path: str, output_path: str, new_domain: str, new_token: str
) -> None:
    if not os.path.exists(template_path):
        default_template = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="2" tvg-logo="https://idc.md" group-title="Общий",ТСВ\n'
            f"https://{new_domain}/hls/2/live1.m3u8?ss={new_token}\n"
        )
        os.makedirs(os.path.dirname(template_path), exist_ok=True)
        with open(template_path, "w", encoding="utf-8") as f:
            f.write(default_template)

    with open(template_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    updated_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("http"):
            line = re.sub(r"^(https?://)[^/]+", rf"\g<1>{new_domain}", line)
            if "ss=" in line:
                line = re.sub(r"([?&]ss=)[^&\s]+", rf"\g<1>{new_token}", line)
            else:
                sep = "&" if "?" in line else "?"
                line = f"{stripped}{sep}ss={new_token}\n"
