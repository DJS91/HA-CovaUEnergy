"""HA-specific unit tests, requiring the optional homeassistant-test dependency group."""

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip(
    "homeassistant", reason="Install the homeassistant-test group on Python 3.14.2+"
)

from homeassistant.exceptions import ConfigEntryAuthFailed  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from custom_components.covau import async_setup_entry, async_unload_entry  # noqa: E402
from custom_components.covau.api import CovaUAuthError, CovaUError  # noqa: E402
from custom_components.covau.config_flow import CovaUConfigFlow  # noqa: E402
from custom_components.covau.coordinator import CovaUCoordinator  # noqa: E402
from custom_components.covau.models import Value  # noqa: E402
from custom_components.covau.sensor import CovaUSensor  # noqa: E402
from custom_components.covau.sensor import async_setup_entry as setup_sensors  # noqa: E402


def entry():
    return SimpleNamespace(
        data={
            "account_number": "0000123",
            "password": "synthetic-password",
            "service_id": "123",
            "service_label": "Example electricity",
        },
        unique_id="0000123_123",
        entry_id="test-entry",
        async_on_unload=MagicMock(),
    )


async def test_setup_first_refresh_then_forward_then_schedule():
    hass, config_entry = MagicMock(), entry()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    with (
        patch("custom_components.covau.async_create_clientsession"),
        patch("custom_components.covau.CovaUClient"),
        patch("custom_components.covau.CovaUCoordinator", return_value=coordinator),
    ):
        assert await async_setup_entry(hass, config_entry)
        assert config_entry.runtime_data is coordinator
        coordinator.async_config_entry_first_refresh.assert_awaited_once()
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()
        coordinator.schedule_hourly.assert_called_once_with(config_entry)
        assert await async_unload_entry(hass, config_entry)
        hass.config_entries.async_unload_platforms.assert_awaited_once()


async def test_scheduler_is_local_hourly_and_unsubscribes():
    # Exercise our exact registration/callback contract; HA owns clock and DST handling.
    coordinator = object.__new__(CovaUCoordinator)
    coordinator.hass = MagicMock()
    coordinator.async_request_refresh = AsyncMock()
    config_entry = entry()
    unsubscribe = MagicMock()
    with patch(
        "custom_components.covau.coordinator.async_track_time_change",
        return_value=unsubscribe,
    ) as track:
        coordinator.schedule_hourly(config_entry)
        args, kwargs = track.call_args
        assert args[0] is coordinator.hass
        assert kwargs == {"minute": 0, "second": 0}
        await args[1](datetime(2026, 10, 25, 6))
        coordinator.async_request_refresh.assert_awaited_once()
        config_entry.async_on_unload.assert_called_once_with(unsubscribe)
        config_entry.async_on_unload.call_args.args[0]()
        unsubscribe.assert_called_once()


@pytest.mark.parametrize(
    ("error", "expected"),
    [(CovaUAuthError("Auth failed"), ConfigEntryAuthFailed), (CovaUError("Offline"), UpdateFailed)],
)
async def test_coordinator_errors(error, expected):
    coordinator = object.__new__(CovaUCoordinator)
    coordinator._client = SimpleNamespace(snapshot=AsyncMock(side_effect=error))
    coordinator._service_id = "123"
    with pytest.raises(expected):
        await coordinator._async_update_data()


async def test_sensor_identity_unknowns_and_privacy():
    coordinator = MagicMock()
    coordinator.data = {
        "example": Value("Daily usage Example", 7.426, "kWh", "energy"),
        "private": Value("Email address", "customer@example.invalid", enabled=False),
        "date": Value("Invoice due date", date(2026, 11, 1), device_class="date"),
    }
    public = CovaUSensor(coordinator, entry(), "example")
    private = CovaUSensor(coordinator, entry(), "private")
    dated = CovaUSensor(coordinator, entry(), "date")
    assert public.unique_id == "0000123_123_example"
    assert public.native_value == 7.426
    assert public.native_unit_of_measurement == "kWh"
    assert public.state_class is None
    assert private.entity_registry_enabled_default is False
    assert dated.native_value == date(2026, 11, 1)
    coordinator.data = {}
    assert public.native_value is None
    assert public.device_class is None
    assert "password" not in public.extra_state_attributes


async def test_new_categories_are_added_on_later_updates():
    coordinator = MagicMock()
    coordinator.data = {"first": Value("First category", 1)}
    config_entry = entry()
    config_entry.runtime_data = coordinator
    add_entities = MagicMock()
    await setup_sensors(MagicMock(), config_entry, add_entities)
    assert len(add_entities.call_args.args[0]) == 1
    on_update = coordinator.async_add_listener.call_args.args[0]
    coordinator.data["second"] = Value("Second category", 2)
    on_update()
    assert add_entities.call_count == 2
    assert [e.unique_id for e in add_entities.call_args.args[0]] == ["0000123_123_second"]
    on_update()
    assert add_entities.call_count == 2


async def test_credentials_then_service_selection():
    flow = CovaUConfigFlow()
    flow.hass = MagicMock()
    flow.context = {"source": "user"}
    session = SimpleNamespace(close=AsyncMock())
    client = SimpleNamespace(
        login=AsyncMock(),
        accounts=AsyncMock(return_value=[{"id": "123", "label": "Example electricity"}]),
    )
    with (
        patch(
            "custom_components.covau.config_flow.async_create_clientsession", return_value=session
        ),
        patch("custom_components.covau.config_flow.CovaUClient", return_value=client),
    ):
        result = await flow.async_step_user(
            {"account_number": "0000123", "password": "synthetic-password"}
        )
    assert result["step_id"] == "account"
    session.close.assert_awaited_once()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    result = await flow.async_step_account({"service_id": "123"})
    flow.async_set_unique_id.assert_awaited_once_with("0000123_123")
    flow._abort_if_unique_id_configured.assert_called_once()
    assert result["data"]["service_id"] == "123"
    assert result["data"]["account_number"] == "0000123"
    assert "accessToken" not in result["data"]


async def test_reauth_updates_existing_entry_only():
    flow = CovaUConfigFlow()
    flow.hass = MagicMock()
    config_entry = entry()
    flow._get_reauth_entry = MagicMock(return_value=config_entry)
    flow.async_update_reload_and_abort = MagicMock(return_value={"type": "abort"})
    await flow.async_step_reauth(config_entry.data)
    session = SimpleNamespace(close=AsyncMock())
    client = SimpleNamespace(
        login=AsyncMock(),
        accounts=AsyncMock(return_value=[{"id": "123", "label": "Example electricity"}]),
    )
    with (
        patch(
            "custom_components.covau.config_flow.async_create_clientsession", return_value=session
        ),
        patch("custom_components.covau.config_flow.CovaUClient", return_value=client),
    ):
        await flow.async_step_user({"account_number": "0000123", "password": "new-synthetic"})
    flow.async_update_reload_and_abort.assert_called_once_with(
        config_entry,
        data_updates={"account_number": "0000123", "password": "new-synthetic"},
    )


@pytest.mark.parametrize(
    ("error", "reason"),
    [(CovaUAuthError("Rejected"), "invalid_auth"), (CovaUError("Offline"), "cannot_connect")],
)
async def test_flow_errors_are_visible(error, reason):
    flow = CovaUConfigFlow()
    flow.hass = MagicMock()
    session = SimpleNamespace(close=AsyncMock())
    client = SimpleNamespace(login=AsyncMock(side_effect=error))
    with (
        patch(
            "custom_components.covau.config_flow.async_create_clientsession", return_value=session
        ),
        patch("custom_components.covau.config_flow.CovaUClient", return_value=client),
    ):
        result = await flow.async_step_user(
            {"account_number": "0000123", "password": "synthetic-password"}
        )
    assert result["errors"] == {"base": reason}
    session.close.assert_awaited_once()
