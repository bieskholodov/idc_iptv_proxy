from datetime import timedelta
import logging
import os
import re

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_INTERVAL,
    CONF_TEMPLATE_PATH,
    CONF_OUTPUT_PATH,
    DEFAULT_TEMPLATE_PATH,
    DEFAULT_OUTPUT_PATH,
)

_LOGGER = logging.getLogger(__name__)

API_HOST = "iptvn.idc.md"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
        "domain": "iptv-nlive1.idc.md",
    }

    async def _get_real_stream_token() -> tuple[str | None, str]:
        login_url = f"https://{API_HOST}/api/json/login"
        async with session.post(
            login_url,
            params={"settings": "all"},
            data={"login": login, "pass": password},
            timeout=15,
        ) as resp:
            resp.raise_for_status()
            login_text = await resp.text()
            login_data = await resp.json(content_type=None)

        if isinstance(login_data, dict) and "error" in login_data:
            raise UpdateFailed(f"Ошибка входа: {login_data['error']}")

        ssid = (
            login_data.get("sid")
            or login_data.get("SSID")
            or login_data.get("token")
        )
        if not ssid:
            m = re.search(r'"(?:sid|SSID|token)"\s*:\s*"([a-f0-9]{32})"', login_text, re.I)
            if m:
                ssid = m.group(1)

        if not ssid:
            raise UpdateFailed(f"API login не вернул SSID: {login_text[:200]}")

        try:
            async with session.get(
                f"https://{API_HOST}/api/json/channel_list",
                params={"SSID": ssid, "icon": "png"},
                timeout=10,
            ) as ch_resp:
                await ch_resp.text()
        except Exception:
            pass

        async with session.get(
            f"https://{API_HOST}/api/json/get_url",
            params={"SSID": ssid, "cid": "2"},
            timeout=15,
        ) as resp:
            resp.raise_for_status()
            url_text = await resp.text()
            url_data = await resp.json(content_type=None)

        if isinstance(url_data, dict) and "error" in url_data:
            raise UpdateFailed(f"Ошибка get_url: {url_data['error']}")

        hls_ticket_url = url_data.get("url") if isinstance(url_data, dict) else None
        if not hls_ticket_url:
            m_url = re.search(r'"url"\s*:\s*"(https?://[^"]+)"', url_text)
            if m_url:
                hls_ticket_url = m_url.group(1).replace("\\/", "/")

        if not hls_ticket_url:
            raise UpdateFailed(f"get_url не вернул HLS-ссылку: {url_text[:200]}")

        domain = state["domain"]
        m_dom = re.search(r"https?://([^/]+)", hls_ticket_url)
        if m_dom:
            domain = m_dom.group(1)

        m_ss = re.search(r"[?&]ss=([a-f0-9]{32})", hls_ticket_url, re.I)
        if m_ss:
            return m_ss.group(1), domain

        async with session.get(hls_ticket_url, timeout=15) as m3u8_resp:
            m3u8_resp.raise_for_status()
            final_url = str(m3u8_resp.url)
            m3u8_body = await m3u8_resp.text()

        m_ss = re.search(
            r"[?&]ss=([a-f0-9]{32})",
            f"{final_url}\n{m3u8_body}",
            re.I,
        )
        if not m_ss:
            raise UpdateFailed("Не удалось извлечь токен ss= из index.m3u8")

        return m_ss.group(1), domain

    async def _ping_m3u8(domain: str, ss_token: str) -> bool:
        """Каждые 5 секунд читаем live1.m3u8?ss=..., как вкладка плеера на паузе."""
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

            if current_ss and await _ping_m3u8(current_domain, current_ss):
                return state

            new_ss, new_domain = await _get_real_stream_token()

            state["ss"] = new_ss
            state["domain"] = new_domain

            await _ping_m3u8(new_domain, new_ss)

            await hass.async_add_executor_job(
                _update_playlist_file,
                template_path,
                output_path,
                new_domain,
                new_ss,
            )
            _LOGGER.info(
                "Активирован реальный токен потока IDC IPTV (ss=%s***), домен %s. Плейлист обновлен.",
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
