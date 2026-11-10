import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

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

class IdcIptvConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            url = f"https://{user_input[CONF_HOST]}/api/json/login"
            params = {
                "login": user_input[CONF_LOGIN],
                "pass": user_input[CONF_PASSWORD],
                "settings": "all",
            }

            try:
                async with session.get(url, params=params, timeout=15) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict):
                            return self.async_create_entry(
                                title=f"IDC IPTV ({user_input[CONF_LOGIN]})",
                                data=user_input,
                            )
                        errors["base"] = "invalid_auth"
                    else:
                        errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(CONF_LOGIN): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=15, max=3600)
                ),
                vol.Optional(CONF_TEMPLATE_PATH, default=DEFAULT_TEMPLATE_PATH): str,
                vol.Optional(CONF_OUTPUT_PATH, default=DEFAULT_OUTPUT_PATH): str,
            }
        )

        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
