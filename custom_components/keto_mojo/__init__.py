"""The Keto-Mojo GK+ glucose/ketone meter integration.

Syncs automatically whenever the meter advertises (i.e. when it is switched
on), pulls every stored reading, keeps a de-duplicated history in .storage and
back-fills long-term statistics at each reading's own timestamp.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import time

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import protocol as p
from .const import CONF_ADDRESS, DOMAIN, NOTIFY_CHAR_UUID, SYNC_COOLDOWN_S, WRITE_CHAR_UUID

try:
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:  # older HA
    StatisticMeanType = None

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "button"]
STORE_VERSION = 1


class KetoMojoCoordinator(DataUpdateCoordinator[dict]):
    """Push-only coordinator; data changes only after a sync."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        # bluetooth APIs key their maps off the uppercase address.
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self._store: Store = Store(hass, STORE_VERSION, f"{DOMAIN}_{entry.entry_id}")
        self._lock = asyncio.Lock()
        self._last_attempt = 0.0
        self.readings: list[p.Reading] = []
        self.serial: str | None = None
        self.glucose_unit: str = p.UNIT_MG_DL
        self.last_sync: datetime | None = None
        self.last_error: str | None = None

    # ---- persistence -------------------------------------------------------

    async def async_load(self) -> None:
        stored = await self._store.async_load() or {}
        self.serial = stored.get("serial")
        self.glucose_unit = stored.get("glucose_unit", p.UNIT_MG_DL)
        if stored.get("last_sync"):
            self.last_sync = dt_util.parse_datetime(stored["last_sync"])
        self.readings = [
            p.Reading(
                datetime.fromisoformat(r["when"]), r["kind"], r["value"], r["unit"],
                r["gki_pair"], r.get("meal", "general"),
            )
            for r in stored.get("readings", [])
        ]
        if self.readings:
            # Idempotent; picks up pairing/derivation changes after an upgrade.
            self._import_statistics()
        self.async_set_updated_data(self._snapshot())

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "serial": self.serial,
                "glucose_unit": self.glucose_unit,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None,
                "readings": [
                    {
                        "when": r.when.isoformat(), "kind": r.kind, "value": r.value,
                        "unit": r.unit, "gki_pair": r.gki_pair, "meal": r.meal,
                    }
                    for r in self.readings
                ],
            }
        )

    async def _async_update_data(self) -> dict:
        return self._snapshot()

    # ---- derived data ------------------------------------------------------

    def _snapshot(self) -> dict:
        def latest(kind: str) -> p.Reading | None:
            matches = [r for r in self.readings if r.kind == kind]
            return max(matches, key=lambda r: r.when) if matches else None

        gki_pairs = p.pair_gki(self.readings)
        latest_gki = max(gki_pairs, key=lambda t: t[1].when) if gki_pairs else None
        return {
            "glucose": latest(p.GLUCOSE),
            "ketone": latest(p.KETONE),
            "gki": latest_gki,
            "readings": sorted(self.readings, key=lambda r: r.when),
        }

    # ---- sync --------------------------------------------------------------

    @callback
    def async_on_advertisement(
        self, service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
    ) -> None:
        if self._lock.locked() or time.monotonic() - self._last_attempt < SYNC_COOLDOWN_S:
            return
        self._last_attempt = time.monotonic()
        self.hass.async_create_task(self._async_sync_quietly(), eager_start=False)

    async def _async_sync_quietly(self) -> None:
        try:
            await self.async_sync()
        except HomeAssistantError as err:
            _LOGGER.warning("keto_mojo: automatic sync failed: %s", err)

    async def async_sync(self) -> None:
        async with self._lock:
            self._last_attempt = time.monotonic()
            try:
                hexbuf = await self._async_download()
            except Exception as err:
                self.last_error = str(err)
                self.async_update_listeners()
                raise HomeAssistantError(f"Keto-Mojo sync failed: {err}") from err

            self.glucose_unit = p.parse_unit(hexbuf) or self.glucose_unit
            self.serial = p.parse_serial(hexbuf) or self.serial
            fetched = p.parse_records(hexbuf, self.glucose_unit)

            known = {r.key for r in self.readings}
            new = [r for r in fetched if r.key not in known]
            # Keep the meter's own order (GKI pairs sit next to each other).
            self.readings.extend(new)
            self.last_sync = dt_util.utcnow()
            self.last_error = None
            await self._async_save()
            if new:
                self._import_statistics()
            _LOGGER.info(
                "keto_mojo: synced %d record(s) from meter, %d new", len(fetched), len(new)
            )
            self.async_set_updated_data(self._snapshot())

    async def _async_download(self) -> str:
        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise HomeAssistantError(
                f"meter {self.address} is not advertising; switch it on and try again"
            )
        chunks: list[str] = []
        got = asyncio.Event()

        def _on_notify(_handle, data: bytearray) -> None:
            chunks.append(bytes(data).hex().upper())
            got.set()

        async def _command(cmd: str, until: str, timeout: float) -> None:
            got.clear()
            start = len(chunks)
            await client.write_gatt_char(WRITE_CHAR_UUID, bytes.fromhex(cmd), response=False)
            deadline = time.monotonic() + timeout
            while until not in "".join(chunks[start:]):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"no reply to {cmd[:16]}")
                got.clear()
                try:
                    await asyncio.wait_for(got.wait(), remaining)
                except asyncio.TimeoutError:
                    pass

        client = await establish_connection(
            BleakClientWithServiceCache, ble_device, f"Keto-Mojo ({self.address})"
        )
        try:
            await client.start_notify(NOTIFY_CHAR_UUID, _on_notify)
            await asyncio.sleep(0.5)
            await _command(p.CMD_GET_UNIT, p.UNIT_REPLY_PREFIX, 8)
            await _command(p.CMD_GET_SERIAL, p.SERIAL_REPLY_PREFIX, 8)
            await _command(p.CMD_FETCH_ALL, p.END_OF_RECORDS, 30)
        finally:
            await client.disconnect()
        return "".join(chunks)

    # ---- long-term statistics ---------------------------------------------

    def _import_statistics(self) -> None:
        """Rebuild hourly external statistics from the full reading history."""
        series: dict[str, tuple[str, str, list[tuple[datetime, float]]]] = {
            "glucose": ("Keto-Mojo Glucose", self.glucose_unit, []),
            "ketone": ("Keto-Mojo Ketone", p.UNIT_MMOL, []),
            "gki": ("Keto-Mojo GKI", None, []),
        }
        tz = dt_util.get_default_time_zone()
        for r in self.readings:
            if r.kind == p.GLUCOSE:
                series["glucose"][2].append((r.when, r.value))
            elif r.kind == p.KETONE:
                series["ketone"][2].append((r.when, r.value))
        for _g, k, gki, _how in p.pair_gki(self.readings):
            series["gki"][2].append((k.when, gki))

        for key, (name, unit, points) in series.items():
            if not points:
                continue
            buckets: dict[datetime, list[float]] = {}
            for when, value in points:
                local = when.replace(tzinfo=tz)
                start = dt_util.as_utc(local).replace(minute=0, second=0, microsecond=0)
                buckets.setdefault(start, []).append(value)
            stats = [
                StatisticData(start=start, mean=sum(v) / len(v), min=min(v), max=max(v))
                for start, v in sorted(buckets.items())
            ]
            meta: dict = {
                "has_sum": False,
                "name": name,
                "source": DOMAIN,
                "statistic_id": f"{DOMAIN}:{key}",
                "unit_of_measurement": unit,
            }
            if StatisticMeanType is not None:
                meta["mean_type"] = StatisticMeanType.ARITHMETIC
                meta["unit_class"] = None
            else:
                meta["has_mean"] = True
            async_add_external_statistics(self.hass, StatisticMetaData(**meta), stats)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = KetoMojoCoordinator(hass, entry)
    await coordinator.async_load()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            coordinator.async_on_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=coordinator.address, connectable=True),
            bluetooth.BluetoothScanningMode.ACTIVE,
        )
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
