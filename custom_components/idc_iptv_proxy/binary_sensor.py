from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([IdcIptvStatusSensor(coordinator, entry)])


class IdcIptvStatusSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Статус сессии"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_session_status"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "IDC IPTV",
            "manufacturer": "IDC",
        }

    @property
    def is_on(self) -> bool:
        if not self.coordinator.last_update_success or not self.coordinator.data:
            return False
        return bool(self.coordinator.data.get("ss"))

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.data:
            return {}
        ss = self.coordinator.data.get("ss")
        return {
            "domain": self.coordinator.data.get("domain"),
            "token_preview": f"{ss[:6]}***" if ss else None,
        }
