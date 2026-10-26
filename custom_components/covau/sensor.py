"""Sensors for daily usage, tariff rates, customer details and billing."""

from datetime import date
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CovaUConfigEntry
from .const import CONF_SERVICE_ID, CONF_SERVICE_LABEL, DOMAIN
from .coordinator import CovaUCoordinator
from .models import Value


async def async_setup_entry(
    hass: HomeAssistant, entry: CovaUConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add new categories/tariffs if the provider introduces them later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = set(coordinator.data) - known
        if new:
            async_add_entities([CovaUSensor(coordinator, entry, key) for key in sorted(new)])
            known.update(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


class CovaUSensor(CoordinatorEntity[CovaUCoordinator], SensorEntity):
    """Read only from a successful coherent coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CovaUCoordinator, entry: CovaUConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        initial = coordinator.data[key]
        self._attr_name = initial.name
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_entity_registry_enabled_default = initial.enabled
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name=f"CovaU {entry.data[CONF_SERVICE_LABEL]}",
            manufacturer="CovaU",
            model="Energy account",
        )
        self._service_id = entry.data[CONF_SERVICE_ID]

    @property
    def _value(self) -> Value | None:
        return self.coordinator.data.get(self._key)

    @property
    def native_value(self) -> str | float | int | date | None:
        return self._value.state if self._value else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._value.unit if self._value else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if self._value and self._value.device_class:
            return SensorDeviceClass(self._value.device_class)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "service_id": self._service_id,
            **(self._value.attributes if self._value else {}),
        }
