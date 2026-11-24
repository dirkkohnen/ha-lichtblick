"""Sensors for the LichtBlick integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import CURRENCY_EURO, EntityCategory, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import DayPrices, PriceData, PriceSlot
from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import LichtblickConfigEntry, LichtblickCoordinator

# Coordinator handles all updates; entities never poll on their own.
PARALLEL_UPDATES = 0

PRICE_UNIT = f"{CURRENCY_EURO}/{UnitOfEnergy.KILO_WATT_HOUR}"


def _eur(value_ct: float | None) -> float | None:
    return None if value_ct is None else round(value_ct / 100, 6)


def _slot_price(slot: PriceSlot | None) -> float | None:
    return slot.price_eur if slot else None


def _slots_as_dicts(day: DayPrices) -> list[dict[str, Any]]:
    return [
        {
            "start": slot.start.isoformat(),
            "end": slot.end.isoformat(),
            "price": slot.price_eur,
        }
        for slot in day.slots
    ]


@dataclass(frozen=True, kw_only=True)
class LichtblickSensorEntityDescription(SensorEntityDescription):
    """Describes a LichtBlick sensor."""

    value_fn: Callable[[PriceData, datetime], float | datetime | None]
    available_fn: Callable[[PriceData], bool] = lambda _: True


SENSORS: tuple[LichtblickSensorEntityDescription, ...] = (
    LichtblickSensorEntityDescription(
        key="current_price",
        translation_key="current_price",
        native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=lambda data, now: _slot_price(data.slot_at(now)),
    ),
    LichtblickSensorEntityDescription(
        key="next_price",
        translation_key="next_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        value_fn=lambda data, now: _slot_price(data.next_slot(now)),
    ),
    LichtblickSensorEntityDescription(
        key="today_average_price",
        translation_key="today_average_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        value_fn=lambda data, _: _eur(data.today.average_ct),
    ),
    LichtblickSensorEntityDescription(
        key="today_min_price",
        translation_key="today_min_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        value_fn=lambda data, _: _slot_price(data.today.min_slot),
    ),
    LichtblickSensorEntityDescription(
        key="today_max_price",
        translation_key="today_max_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        value_fn=lambda data, _: _slot_price(data.today.max_slot),
    ),
    LichtblickSensorEntityDescription(
        key="today_min_price_time",
        translation_key="today_min_price_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, _: (
            data.today.min_slot.start if data.today.min_slot else None
        ),
    ),
    LichtblickSensorEntityDescription(
        key="today_max_price_time",
        translation_key="today_max_price_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data, _: (
            data.today.max_slot.start if data.today.max_slot else None
        ),
    ),
    LichtblickSensorEntityDescription(
        key="today_cheapest_window_start",
        translation_key="today_cheapest_window_start",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda data, _: (
            data.today.cheapest_window.start if data.today.cheapest_window else None
        ),
    ),
    LichtblickSensorEntityDescription(
        key="today_cheapest_window_end",
        translation_key="today_cheapest_window_end",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
        value_fn=lambda data, _: (
            data.today.cheapest_window.end if data.today.cheapest_window else None
        ),
    ),
    LichtblickSensorEntityDescription(
        key="tomorrow_average_price",
        translation_key="tomorrow_average_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        available_fn=lambda data: data.tomorrow.available,
        value_fn=lambda data, _: _eur(data.tomorrow.average_ct),
    ),
    LichtblickSensorEntityDescription(
        key="tomorrow_min_price",
        translation_key="tomorrow_min_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        available_fn=lambda data: data.tomorrow.available,
        value_fn=lambda data, _: _slot_price(data.tomorrow.min_slot),
    ),
    LichtblickSensorEntityDescription(
        key="tomorrow_max_price",
        translation_key="tomorrow_max_price",
        native_unit_of_measurement=PRICE_UNIT,
        suggested_display_precision=4,
        available_fn=lambda data: data.tomorrow.available,
        value_fn=lambda data, _: _slot_price(data.tomorrow.max_slot),
    ),
    LichtblickSensorEntityDescription(
        key="tomorrow_min_price_time",
        translation_key="tomorrow_min_price_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        available_fn=lambda data: data.tomorrow.available,
        value_fn=lambda data, _: (
            data.tomorrow.min_slot.start if data.tomorrow.min_slot else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LichtblickConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up LichtBlick sensors from a config entry."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        LichtblickSensor(coordinator, description) for description in SENSORS
    ]
    entities.append(LichtblickPriceListSensor(coordinator))
    async_add_entities(entities)


class LichtblickEntity(CoordinatorEntity[LichtblickCoordinator]):
    """Base entity for LichtBlick."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: LichtblickCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{coordinator.contract_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.contract_id))},
            name=entry.title,
            manufacturer=MANUFACTURER,
            model="ÖkoStrom Dynamic",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.lichtblick.de/",
        )


class LichtblickSensor(LichtblickEntity, SensorEntity):
    """A LichtBlick price sensor."""

    entity_description: LichtblickSensorEntityDescription

    def __init__(
        self,
        coordinator: LichtblickCoordinator,
        description: LichtblickSensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return if the entity is available."""
        return super().available and self.entity_description.available_fn(
            self.coordinator.data
        )

    @property
    def native_value(self) -> float | datetime | None:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data, dt_util.now())


class LichtblickPriceListSensor(LichtblickEntity, SensorEntity):
    """Number of published slots, with the full price list as attributes.

    The price lists are meant for charts (e.g. ApexCharts) and are excluded
    from the recorder to keep the database small. For automations the
    ``lichtblick.get_prices`` action is the preferred way to read them.
    """

    _attr_translation_key = "price_slots"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({"prices_today", "prices_tomorrow"})

    def __init__(self, coordinator: LichtblickCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "price_slots")

    @property
    def native_value(self) -> int:
        """Return the number of known price slots (today + tomorrow)."""
        return len(self.coordinator.data.slots)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the price lists."""
        data = self.coordinator.data
        return {
            "prices_today": _slots_as_dicts(data.today),
            "prices_tomorrow": _slots_as_dicts(data.tomorrow),
        }
