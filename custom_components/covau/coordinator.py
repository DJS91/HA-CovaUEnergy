"""Hourly local-time refresh of CovaU data."""

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import CovaUAuthError, CovaUClient, CovaUError
from .const import DOMAIN
from .models import Value, normalize

_LOGGER = logging.getLogger(__name__)


class CovaUCoordinator(DataUpdateCoordinator[dict[str, Value]]):
    """Refresh on setup and at the start of each hour in Home Assistant's timezone."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: CovaUClient,
        service_id: str,
    ) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=None)
        self._client = client
        self._service_id = service_id

    def schedule_hourly(self, entry: ConfigEntry) -> None:
        """Tie the local wall-clock listener to this entry's lifecycle."""
        entry.async_on_unload(
            async_track_time_change(self.hass, self._scheduled_refresh, minute=0, second=0)
        )

    async def _scheduled_refresh(self, now: datetime) -> None:
        await self.async_request_refresh()

    async def _async_update_data(self) -> dict[str, Value]:
        today = dt_util.now().date()
        try:
            snapshot = await self._client.snapshot(self._service_id, today)
            return normalize(snapshot, self._service_id, today)
        except CovaUAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CovaUError as err:
            raise UpdateFailed(str(err)) from err
