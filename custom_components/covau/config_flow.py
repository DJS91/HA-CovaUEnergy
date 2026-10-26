"""Account login, service selection and reauthentication."""

from typing import Any

import aiohttp
import probatio
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CovaUAuthError, CovaUClient, CovaUDataError, CovaUError
from .const import (
    CONF_ACCOUNT_NUMBER,
    CONF_SERVICE_ID,
    CONF_SERVICE_LABEL,
    DOMAIN,
)


class CovaUConfigFlow(ConfigFlow, domain=DOMAIN):
    """Authenticate before offering only the user's accessible services."""

    VERSION = 1

    def __init__(self) -> None:
        self._credentials: dict[str, str] = {}
        self._accounts: list[dict[str, str]] = []
        self._reauth = False

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
                return self.async_create_entry(
                    title=f"CovaU {selected['label']}",
                    data={
                        **self._credentials,
                        CONF_SERVICE_ID: service_id,
                        CONF_SERVICE_LABEL: selected["label"],
                    },
                )
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth = True
        return await self.async_step_user()
