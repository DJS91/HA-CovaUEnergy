"""Unofficial CovaU integration."""

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CovaUClient
from .const import CONF_ACCOUNT_NUMBER, CONF_SERVICE_ID
from .coordinator import CovaUCoordinator

type CovaUConfigEntry = ConfigEntry[CovaUCoordinator]

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: CovaUConfigEntry) -> bool:
    """Initialize an entry without sharing cookies with other integrations."""
    session = async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar())
    client = CovaUClient(session, entry.data[CONF_ACCOUNT_NUMBER], entry.data[CONF_PASSWORD])
    coordinator = CovaUCoordinator(hass, entry, client, entry.data[CONF_SERVICE_ID])
    entry.runtime_data = coordinator
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.schedule_hourly(entry)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CovaUConfigEntry) -> bool:
    """Unload entities; HA releases the entry's timer and HTTP session."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
