from datetime import timedelta
import logging
import os
import re
from urllib.parse import unquote, quote

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
    DEFAULT_TEMPLATE_PATH,
    DEFAULT_OUTPUT_PATH,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host = entry.data.get(CONF_HOST, DEFAULT_HOST)
    login = entry.data[CONF_LOGIN]
    password = entry.data[CONF_PASSWORD]
    interval = min(entry.data.get(CONF_INTERVAL, 5), 8)
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
        "ss": None,
        "domain": "iptv-live.idc.md",
    }

    async def _login_and_get_token() -> tuple[str | None, str]:
        # 1. Твой оригинальный запрос авторизации (он всегда работал)
        login_url = f"https://{host}/api/json/login"
        params = {
            "login": login,
            "pass": password,
            "settings": "all",
        }
        async with session.get(url=login_url, params=params, timeout=15) as resp:
            resp.raise_for_status()
            login_text = await resp.text()
            login_data = await resp.json(content_type=None)

        ssid = (
            login_data.get("sid")
            or login_data.get("SSID")
            or login_data.get("token")
        )
        if not ssid:
            match = re.search(
                r'"(?:sid|SSID|token)"\s*:\s*"([a-f0-9]{32})"',
                login_text,
                re.IGNORECASE,
            )
            if match:
                ssid = match.group(1)

        if not ssid:
            raise UpdateFailed("API IDC не вернул токен сессии (sid)")

        domain = state["domain"]
        domains = re.findall(r"(iptv-[a-z0-9-]+\.idc\.md)", login_text, re.IGNORECASE)
        stream_domains = [d for d in domains if not d.lower().startswith("iptvn.")]
        if stream_domains:
            domain = stream_domains

        # 2. Получаем ticket из get_url (даже если там http/ts://...)
        async with session.get(
            f"https://{host}/api/json/get_url",
            params={"SSID": ssid, "cid": "2"},
            timeout=15,
        ) as resp:
            resp.raise_for_status()
            url_text = await resp.text()

        m_ticket = re.search(r"ticket=([^&\"':+\s]+)", url_text)
        if not m_ticket:
            raise UpdateFailed(f"В ответе get_url не найден ticket: {url_text[:200]}")

        raw_ticket = unquote(m_ticket.group(1))
        encoded_ticket = quote(raw_ticket, safe="")

        # 3. Обмениваем ticket на живой ss= через https://<domain>/hls/2/index.m3u8?ticket=...
        index_url = f"https://{domain}/hls/2/index.m3u8?ticket={encoded_ticket}"
        async with session.get(index_url, timeout=15) as m3u8_resp:
            m3u8_resp.raise_for_status()
            final_url = str(m3u8_resp.url)
            m3u8_body = await m3u8_resp.text()

        m_ss = re.search(
            r"[?&]ss=([a-f0-9]{32})",
            f"{final_url}\n{m3u8_body}",
            re.IGNORECASE,
        )
        if not m_ss:
            raise UpdateFailed("Не удалось получить ss= из index.m3u8 по ticket")

        return m_ss.group(1), domain

    async def _check_stream_alive(domain: str, ss_token: str) -> bool:
        """Каждые 5 секунд читаем live1.m3u8?ss=..., чтобы сервер держал сессию онлайн."""
        test_url = f"https://{domain}/hls/2/live1.m3u8?ss={ss_token}"
        try:
            async with session.get(test_url, timeout=5) as resp:
                if resp.status == 200:
                    body = await resp.text()
                    return "#EXTM3U" in body
        except Exception:
            pass
        return False

    async def _async_update_data():
        try:
            current_ss = state["ss"]
            current_domain = state["domain"]

            if current_ss and await _check_stream_alive(current_domain, current_ss):
                return state

            new_ss, new_domain = await _login_and_get_token()
            if not new_ss:
                raise UpdateFailed("API IDC не вернул токен потока (ss)")

            state["ss"] = new_ss
            state["domain"] = new_domain

            await _check_stream_alive(new_domain, new_ss)

            await hass.async_add_executor_job(
                _update_playlist_file,
                template_path,
                output_path,
                new_domain,
                new_ss,
            )
            _LOGGER.info(
                "Получен живой токен IDC IPTV (ss=%s***), домен %s. Плейлист обновлен.",
                new_ss[:6],
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
