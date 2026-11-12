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
    interval = min(entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL), 15)
    template_path = entry.data.get(CONF_TEMPLATE_PATH, DEFAULT_TEMPLATE_PATH)
    output_path = entry.data.get(CONF_OUTPUT_PATH, DEFAULT_OUTPUT_PATH)

    cookie_jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(
        cookie_jar=cookie_jar,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"https://{host}/",
            "Origin": f"https://{host}",
            "X-Requested-With": "XMLHttpRequest",
        },
    )

    state = {
        "token": None,
        "domain": "iptv-live.idc.md",
    }

    async def _login_and_get_token() -> tuple[str | None, str]:
        # Очищаем старые куки перед новой авторизацией, чтобы избежать конфликта сессий
        session.cookie_jar.clear()

        # 1. Заходим на главную страницу портала, чтобы получить первичные сессионные куки
        try:
            async with session.get(
                f"https://{host}/",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as init_resp:
                await init_resp.text()
        except Exception as err:
            _LOGGER.debug("Не удалось открыть главную страницу портала: %s", err)

        # 2. Выполняем авторизацию
        url = f"https://{host}/api/json/login"
        params = {
            "login": login,
            "pass": password,
            "settings": "all",
        }
        async with session.get(
            url,
            params=params,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            text = await resp.text()
            data = await resp.json(content_type=None)

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
                r'"(?:sid|ss|token)"\s*:\s*"([a-f0-9]{32})"',
                text,
                re.IGNORECASE,
            )
            if match:
                token = match.group(1)

        # Записываем токен в cookie_jar на случай, если API не выставил его через Set-Cookie
        if token:
            session.cookie_jar.update_cookies({"SSID": token, "sid": token, "ss": token})

        domain = state["domain"]
        domains = re.findall(r"(iptv-[a-z0-9-]+\.idc\.md)", text, re.IGNORECASE)
        stream_domains = [d for d in domains if not d.lower().startswith("iptvn.")]
        if stream_domains:
            domain = stream_domains[0]

        # 3. Имитируем действия браузера после входа: запрашиваем список каналов/настройки
        if token:
            for endpoint in ("/api/json/channels", "/api/json/get_url"):
                try:
                    async with session.get(
                        f"https://{host}{endpoint}",
                        params={"sid": token, "ss": token, "cid": "1"},
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as api_resp:
                        await api_resp.text()
                except Exception:
                    pass

        return token, domain

    async def _keep_portal_alive(token: str) -> None:
        """Пингуем API веб-портала, чтобы сервер не закрывал веб-сессию."""
        for endpoint in ("/api/json/ping", "/api/json/status", "/api/json/channels"):
            try:
                async with session.get(
                    f"https://{host}{endpoint}",
                    params={"sid": token, "ss": token},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        await resp.text()
                        return
            except Exception:
                continue

    async def _check_stream_alive(domain: str, token: str) -> bool:
        """Проверяем доступность плейлиста и скачиваем последний .ts сегмент целиком."""
        test_url = f"https://{domain}/hls/2/live1.m3u8?ss={token}"
        try:
            async with session.get(
                test_url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.debug("HLS манифест вернул статус %s", resp.status)
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

                # Скачиваем последний .ts сегмент целиком без Range, чтобы балансировщик засчитал просмотр
                ts_url = urljoin(test_url, ts_lines[-1])
                async with session.get(
                    ts_url,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as ts_resp:
                    if ts_resp.status == 200:
                        chunk = await ts_resp.content.read(65536)
                        return len(chunk) > 0
                    _LOGGER.debug("TS сегмент вернул статус %s", ts_resp.status)
        except Exception as err:
            _LOGGER.debug("Ошибка проверки стрима: %s", err)
        return False

    async def _async_update_data():
        try:
            current_token = state["token"]
            current_domain = state["domain"]

            if current_token:
                await _keep_portal_alive(current_token)
                if await _check_stream_alive(current_domain, current_token):
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

            is_alive = await _check_stream_alive(new_domain, new_token)
            if not is_alive:
                _LOGGER.warning(
                    "Новый токен %s*** получен, но стрим пока не отвечает. Проверьте лимит устройств на аккаунте.",
                    new_token[:6],
                )

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
            '#EXTINF:-1 tvg-id="1" tvg-logo="https://idc.md" group-title="Общий",Россия 1\n'
            f"https://{new_domain}/hls/1/live1.m3u8?ss={new_token}\n"
            '#EXTINF:-1 tvg-id="2" tvg-logo="https://idc.md" group-title="Общий",ТСВ\n'
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
