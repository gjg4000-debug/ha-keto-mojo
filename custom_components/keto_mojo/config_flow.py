"""Config flow for the Keto-Mojo GK+ meter."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.helpers.device_registry import format_mac

from .const import CONF_ADDRESS, DEFAULT_NAME, DOMAIN


class KetoMojoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._discovered: BluetoothServiceInfoBleak | None = None

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._discovered = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name.strip("\x00")}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        assert self._discovered is not None
        if user_input is not None:
            return self._create(self._discovered.address)
        self._set_confirm_only()
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={"name": self._discovered.name.strip("\x00")},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(format_mac(address))
            self._abort_if_unique_id_configured()
            return self._create(address)

        current = self._async_current_ids()
        found = {
            info.address: f"{info.name.strip(chr(0))} ({info.address})"
            for info in async_discovered_service_info(self.hass, connectable=True)
            if (info.name or "").startswith("Keto-Mojo")
            and format_mac(info.address) not in current
        }
        if not found:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(found)}),
        )

    def _create(self, address: str):
        return self.async_create_entry(
            title=DEFAULT_NAME, data={CONF_ADDRESS: format_mac(address)}
        )
