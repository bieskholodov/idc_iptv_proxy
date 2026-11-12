from datetime import timedelta
import logging
import os
import re
from urllib.parse import urljoin

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
    interval = min(entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL), 20)
    template_path = entry.data.get(CONF_TEMPLATE_PATH, DEFAULT_TEMPLATE_PATH)
    output_path = entry.data.get(CONF_OUTPUT_PATH, DEFAULT_OUTPUT_PATH)

    cookie_jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(
        cookie_jar=cookie_jar,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": "https://iptv.idc.md/",
            "Origin": "https://iptv.idc.md",
        },
    )

    state = {
        "token": None,
        "domain": "iptv-live.idc.md",
    }

    async def _login_and_get_token() -> tuple[str | None, str]:
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

        token = (
            data.get("sid")
            or data.get("ss")
            or data.get("token")
            or (data.get("settings", {}).get("ss") if isinstance(data.get("settings"), dict) else None)
        )
        if not token:
            match = re.search(r'"(?:sid|ss|token)"\s*:\s*"([a-f0-9]{32})"', text, re.IGNORECASE)
            if match:
                token = match.group(1)

        domain = state["domain"]
        domains = re.findall(r"(iptv-[a-z0-9-]+\.idc\.md)", text, re.IGNORECASE)
        stream_domains = [d for d in domains if not d.lower().startswith("iptvn.")]
        if stream_domains:
            domain = stream_domains[0]

        return token, domain

    async def _check_stream_alive(domain: str, token: str) -> bool:
        """Качаем live1.m3u8 и 1 КБ последнего .ts сегмента, чтобы сервер держал сессию онлайн."""
        test_url = f"https://{domain}/hls/2/live1.m3u8?ss={token}"
        headers = {"Cookie": f"SSID={token}"}
        try:
            async with session.get(test_url, headers=headers, timeout=10) as resp:
                if resp.status != 200:
                    return False
                body = await resp.text()
                if "#EXTM3U" not in body:
                    return False

                ts_lines = [
                    line.strip()
                    for line in body.splitlines()
                    if line.strip() and not line.startswith("#")
                ]
                if not ts_lines:
                    return False

                ts_url = urljoin(test_url, ts_lines[-1])
                ts_headers = {**headers, "Range": "bytes=0-1023"}
                async with session.get(ts_url, headers=ts_headers, timeout=10) as ts_resp:
                    if ts_resp.status in (200, 206):
                        await ts_resp.read()
                        return True
        except Exception:
            pass
        return False

    async def _async_update_data():
        try:
            current_token = state["token"]
            current_domain = state["domain"]

            if current_token and await _check_stream_alive(current_domain, current_token):
                _LOGGER.debug(
                    "Сессия IDC IPTV активна (токен %s***, домен %s)",
                    current_token[:6],
                    current_domain,
                )
                return state

            new_token, new_domain = await _login_and_get_token()
            if not new_token:
                raise UpdateFailed("API IDC не вернул токен сессии (sid)")

            state["token"] = new_token
            state["domain"] = new_domain

            # Сразу дергаем первый сегмент с новым токеном, чтобы активировать сессию на стриме
            await _check_stream_alive(new_domain, new_token)

            await hass.async_add_executor_job(
                _update_playlist_file,
                template_path,
                output_path,
                new_domain,
                new_token,
            )
            _LOGGER.info(
                "Получен новый токен IDC IPTV (%s***), домен %s. Плейлист обновлен.",
                new_token[:6],
                new_domain,
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
    }
    return True


def _update_playlist_file(
    template_path: str, output_path: str, new_domain: str, new_token: str
) -> None:
    if not os.path.exists(template_path):
        _LOGGER.warning(
            "Шаблон %s не найден, создаю базовый плейлист по умолчанию.", template_path
        )
        default_template = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="1" tvg-logo="https://iptvn.idc.md/img/ico/png/1_512.png" group-title="Общий",Россия 1\n'
            f"https://{new_domain}/hls/1/live1.m3u8?ss={new_token}\n"
            '#EXTINF:-1 tvg-id="2" tvg-logo="https://iptvn.idc.md/img/ico/png/2_512.png" group-title="Общий",ТСВ\n'
            f"https://{new_domain}/hls/2/live1.m3u8?ss={new_token}\n"
        )
        template_dir = os.path.dirname(template_path)
        if template_dir:
            os.makedirs(template_dir, exist_ok=True)
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
        updated_lines.append(line)

    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        file.writelines(updated_lines)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if entry_data and "session" in entry_data:
        await entry_data["session"].close()
    return True
