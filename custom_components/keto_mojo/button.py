"""Sync-now button for the Keto-Mojo GK+ meter."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import KetoMojoCoordinator
from .const import DOMAIN
from .sensor import device_info


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KetoMojoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SyncButton(coordinator)])


class SyncButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Sync now"
    _attr_icon = "mdi:bluetooth-transfer"

    def __init__(self, coordinator: KetoMojoCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address}_sync"
        self._attr_device_info = device_info(coordinator)

    async def async_press(self) -> None:
        await self._coordinator.async_sync()
