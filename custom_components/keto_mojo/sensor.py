"""Sensors for the Keto-Mojo GK+ meter."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from . import KetoMojoCoordinator
from . import protocol as p
from .const import DOMAIN

RECENT_LIMIT = 30


def device_info(coordinator: KetoMojoCoordinator) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.address)},
        name="Keto-Mojo",
        manufacturer="Keto-Mojo (VivaChek)",
        model="GK+",
        serial_number=coordinator.serial,
    )


def _local(when):
    return when.replace(tzinfo=dt_util.get_default_time_zone())


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: KetoMojoCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            GlucoseSensor(coordinator),
            KetoneSensor(coordinator),
            GkiSensor(coordinator),
            LastReadingSensor(coordinator),
            LastSyncSensor(coordinator),
        ]
    )


class _Base(CoordinatorEntity[KetoMojoCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _key = ""

    def __init__(self, coordinator: KetoMojoCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.address}_{self._key}"
        self._attr_device_info = device_info(coordinator)


class _ReadingSensor(_Base):
    _kind = ""

    @property
    def _reading(self) -> p.Reading | None:
        return (self.coordinator.data or {}).get(self._kind)

    @property
    def native_value(self) -> float | None:
        r = self._reading
        return r.value if r else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        r = self._reading
        return r.unit if r else None

    @property
    def extra_state_attributes(self) -> dict:
        r = self._reading
        readings = (self.coordinator.data or {}).get("readings", [])
        recent = [x for x in readings if x.kind == self._kind][-RECENT_LIMIT:]
        return {
            "measured_at": _local(r.when).isoformat() if r else None,
            "meal": r.meal if r else None,
            "recent": [{"when": x.when.isoformat(), "value": x.value} for x in recent],
        }


class GlucoseSensor(_ReadingSensor):
    _key = "glucose"
    _kind = p.GLUCOSE
    _attr_name = "Glucose"
    _attr_icon = "mdi:water"
    _attr_suggested_display_precision = 0


class KetoneSensor(_ReadingSensor):
    _key = "ketone"
    _kind = p.KETONE
    _attr_name = "Ketone"
    _attr_icon = "mdi:alpha-k-circle-outline"
    _attr_suggested_display_precision = 1


class GkiSensor(_Base):
    _key = "gki"
    _attr_name = "GKI"
    _attr_icon = "mdi:scale-balance"
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        t = (self.coordinator.data or {}).get("gki")
        return t[2] if t else None

    @property
    def extra_state_attributes(self) -> dict:
        t = (self.coordinator.data or {}).get("gki")
        if not t:
            return {}
        g, k, _gki, how = t
        return {
            "measured_at": _local(max(g.when, k.when)).isoformat(),
            "paired_by": "meter (GKI mode)" if how == "meter" else "time (within 30 min)",
            "glucose": g.value,
            "glucose_unit": g.unit,
            "ketone": k.value,
        }


class LastReadingSensor(_Base):
    _key = "last_reading"
    _attr_name = "Last reading"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        readings = (self.coordinator.data or {}).get("readings", [])
        return _local(readings[-1].when) if readings else None

    @property
    def extra_state_attributes(self) -> dict:
        readings = (self.coordinator.data or {}).get("readings", [])
        return {"total_readings": len(readings)}


class LastSyncSensor(_Base):
    _key = "last_sync"
    _attr_name = "Last sync"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = True

    @property
    def native_value(self):
        return self.coordinator.last_sync

    @property
    def extra_state_attributes(self) -> dict:
        return {"last_error": self.coordinator.last_error}
