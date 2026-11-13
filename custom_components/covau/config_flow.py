"""Account login, service selection and reauthentication."""

from __future__ import annotations

from typing import Any

import aiohttp
import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util

from .api import CovaUAuthError, CovaUClient, CovaUDataError, CovaUError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_COST_CONFIG,
    CONF_SERVICE_ID,
    CONF_SERVICE_LABEL,
    DOMAIN,
)
from .models import normalize, stable_key

_NONE = "__none__"


class CostFlow:
    """Shared optional cost configuration for setup and options."""

    async def _cost_setup(
        self, user_input: dict[str, Any] | None, step_id: str, current: dict[str, Any]
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["enabled"]:
                return self._finish_cost({})
            day = user_input["billing_day"]
            if (
                isinstance(day, bool)
                or not isinstance(day, (int, float))
                or not 1 <= day <= 31
                or int(day) != day
            ):
                errors["base"] = "invalid_billing_day"
            else:
                session = async_create_clientsession(self.hass, cookie_jar=aiohttp.DummyCookieJar())
                try:
                    client = CovaUClient(
                        session,
                        self._cost_entry_data[CONF_ACCOUNT_NUMBER],
                        self._cost_entry_data[CONF_PASSWORD],
                    )
                    today = dt_util.now().date()
                    snapshot = await client.snapshot(self._cost_entry_data[CONF_SERVICE_ID], today)
                    self._cost_values = normalize(
                        snapshot, self._cost_entry_data[CONF_SERVICE_ID], today
                    )
                except CovaUAuthError:
                    errors["base"] = "invalid_auth"
                except CovaUDataError:
                    errors["base"] = "invalid_response"
                except CovaUError:
                    errors["base"] = "cannot_connect"
                else:
                    self._billing_day = int(day)
                    self._old_mappings = current.get("mappings", {})
                    self._old_supply = current.get("supply", [])
                    self._mappings: dict[str, str] = {}
                    self._usage_keys = sorted(
                        {
                            key
                            for key in self._cost_values
                            if key.startswith("usage_") and key != "usage_date"
                        }
                        | set(self._old_mappings)
                    )
                    self._usage_index = 0
                    return await self.async_step_cost_mapping()
                finally:
                    await session.close()
        return self.async_show_form(
            step_id=step_id,
            data_schema=probatio.Schema(
                {
                    probatio.Required("enabled", default=bool(current)): BooleanSelector(),
                    probatio.Required(
                        "billing_day", default=current.get("billing_day", 1)
                    ): NumberSelector(
                        NumberSelectorConfig(min=1, max=31, step=1, mode=NumberSelectorMode.BOX)
                    ),
                }
            ),
            errors=errors,
        )

    def _price_options(
        self, units: set[str], *, include_rate: bool = False
    ) -> list[SelectOptionDict]:
        options = []
        for key, value in self._cost_values.items():
            tariff = value.attributes.get("tariff")
            if (
                isinstance(tariff, str)
                and value.unit in units
                and key == stable_key("price", tariff, value.unit, "unitRateIncGst")
            ):
                if include_rate:
                    rate = value.state if value.state is not None else "Unknown"
                    label = f"{tariff} ({rate} {value.unit})"
                else:
                    label = f"{tariff} ({value.unit}, inc GST)"
                options.append(SelectOptionDict(value=key, label=label))
        return sorted(options, key=lambda option: option["label"])

    async def async_step_cost_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._usage_index >= len(self._usage_keys):
            return await self.async_step_cost_supply()
        key = self._usage_keys[self._usage_index]
        usage = self._cost_values.get(key)
        if user_input is not None:
            selected = user_input["tariff"]
            if selected != _NONE:
                self._mappings[key] = selected
            self._usage_index += 1
            return await self.async_step_cost_mapping()
        units = {"c/kWh", "$/kWh", "c/MJ", "$/MJ"}
        if usage and usage.unit:
            units = {unit for unit in units if unit.endswith(f"/{usage.unit}")}
        options = [SelectOptionDict(value=_NONE, label="Do not map")] + self._price_options(
            units, include_rate=True
        )
        previous = self._old_mappings.get(key, _NONE)
        if previous != _NONE and not any(option["value"] == previous for option in options):
            options.append(
                SelectOptionDict(value=previous, label="Previously selected tariff (unavailable)")
            )
        return self.async_show_form(
            step_id="cost_mapping",
            description_placeholders={
                "category": usage.name if usage else "Previously selected usage (unavailable)"
            },
            data_schema=probatio.Schema(
                {
                    probatio.Required("tariff", default=previous): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_cost_supply(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._price_options({"c/Day", "$/Day"})
        for key in self._old_supply:
            if not any(option["value"] == key for option in options):
                options.append(
                    SelectOptionDict(
                        value=key, label="Previously selected supply charge (unavailable)"
                    )
                )
        errors = {}
        if user_input is not None:
            supply = user_input.get("supply", [])
            if self._mappings or supply:
                return self._finish_cost(
                    {"billing_day": self._billing_day, "mappings": self._mappings, "supply": supply}
                )
            errors["base"] = "no_cost_mappings"
        schema = {}
        if options:
            schema[probatio.Optional("supply", default=self._old_supply)] = SelectSelector(
                SelectSelectorConfig(options=options, multiple=True)
            )
        return self.async_show_form(
            step_id="cost_supply", data_schema=probatio.Schema(schema), errors=errors
        )


class CovaUConfigFlow(CostFlow, ConfigFlow, domain=DOMAIN):
    """Authenticate before offering only the user's accessible services."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._accounts: list[dict[str, str]] = []
        self._reauth = False
        self._cost_entry_data: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CovaUOptionsFlow:
        return CovaUOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            account = str(user_input[CONF_ACCOUNT_NUMBER]).strip()
            password = user_input[CONF_PASSWORD]
            if not account or not password:
                errors["base"] = "invalid_auth"
            else:
                session = async_create_clientsession(self.hass, cookie_jar=aiohttp.DummyCookieJar())
                try:
                    client = CovaUClient(session, account, password)
                    await client.login()
                    accounts = await client.accounts()
                except CovaUAuthError:
                    errors["base"] = "invalid_auth"
                except CovaUDataError:
                    errors["base"] = "invalid_response"
                except CovaUError:
                    errors["base"] = "cannot_connect"
                else:
                    if not accounts:
                        errors["base"] = "no_accounts"
                    elif self._reauth:
                        entry = self._get_reauth_entry()
                        if account != entry.data[CONF_ACCOUNT_NUMBER]:
                            errors["base"] = "wrong_account"
                        elif entry.data[CONF_SERVICE_ID] not in {a["id"] for a in accounts}:
                            errors["base"] = "service_unavailable"
                        else:
                            return self.async_update_reload_and_abort(
                                entry,
                                data_updates={
                                    CONF_ACCOUNT_NUMBER: account,
                                    CONF_PASSWORD: password,
                                },
                            )
                    else:
                        self._credentials = {
                            CONF_ACCOUNT_NUMBER: account,
                            CONF_PASSWORD: password,
                        }
                        self._accounts = accounts
                        return await self.async_step_account()
                finally:
                    await session.close()
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ACCOUNT_NUMBER): TextSelector(),
                    probatio.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_account(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            service_id = user_input[CONF_SERVICE_ID]
            selected = next((a for a in self._accounts if a["id"] == service_id), None)
            if selected is None:
                errors["base"] = "service_unavailable"
            else:
                await self.async_set_unique_id(
                    f"{self._credentials[CONF_ACCOUNT_NUMBER]}_{service_id}"
                )
                self._abort_if_unique_id_configured()
                self._cost_entry_data = {
                    **self._credentials,
                    CONF_SERVICE_ID: service_id,
                    CONF_SERVICE_LABEL: selected["label"],
                }
                return await self.async_step_cost_setup()
        return self.async_show_form(
            step_id="account",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SERVICE_ID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=a["id"], label=a["label"])
                                for a in self._accounts
                            ]
                        )
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_cost_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._cost_setup(user_input, "cost_setup", {})

    def _finish_cost(self, config: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"CovaU {self._cost_entry_data[CONF_SERVICE_LABEL]}",
            data={**self._cost_entry_data, CONF_COST_CONFIG: config},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth = True
        return await self.async_step_user()


class CovaUOptionsFlow(CostFlow, OptionsFlowWithReload):
    """Edit optional cost estimates for an existing service."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        self._cost_entry_data = self.config_entry.data
        current = self.config_entry.options.get(
            CONF_COST_CONFIG, self.config_entry.data.get(CONF_COST_CONFIG, {})
        )
        return await self._cost_setup(user_input, "init", current)

    def _finish_cost(self, config: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data={CONF_COST_CONFIG: config})
