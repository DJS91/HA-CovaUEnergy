"""CovaU HTTP client. Authentication material stays in memory."""

from datetime import date, timedelta
from http.cookies import SimpleCookie
from typing import Any

import aiohttp

from .const import API_BASE, PORTAL_BASE


class CovaUError(Exception):
    """An API request or response failed."""


class CovaUAuthError(CovaUError):
    """Credentials were rejected or access was revoked."""


class CovaUDataError(CovaUError):
    """The provider returned an unsupported response."""


def object_value(value: Any, section: str) -> dict[str, Any]:
    """Require a JSON object without exposing its contents in errors."""
    if not isinstance(value, dict):
        raise CovaUDataError(f"Expected an object for {section}")
    return value


def rows_value(value: Any, section: str) -> list[dict[str, Any]]:
    """Require a JSON array of objects."""
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise CovaUDataError(f"Expected an array of objects for {section}")
    return value


def identifier(value: Any, field: str) -> str:
    """Require a nonempty scalar identifier."""
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).strip():
        raise CovaUDataError(f"Missing or invalid {field}")
    return str(value).strip()


class CovaUClient:
    """Use a dedicated, cookie-less session supplied by the integration."""

    def __init__(self, session: aiohttp.ClientSession, account: str, password: str) -> None:
        self._session = session
        self._account = account
        self._password = password
        self._token: str | None = None
        self._party: str | None = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        portal: bool = False,
        authenticated: bool = True,
        params: dict[str, str] | None = None,
        body: dict[str, str] | None = None,
    ) -> Any:
        headers = {"Accept": "application/json"}
        if authenticated:
            if not self._token or not self._party:
                raise CovaUAuthError("Authentication is required")
            if portal:
                cookies = SimpleCookie()
                cookies["covauToken"] = self._token
                cookies["seqPartyId"] = self._party
                headers["Cookie"] = cookies.output(header="", sep=";").strip()
                headers["Referer"] = f"{PORTAL_BASE}/my-rates/"
            else:
                headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with self._session.request(
                method,
                f"{PORTAL_BASE if portal else API_BASE}{path}",
                headers=headers,
                params=params,
                json=body,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status in (401, 403):
                    raise CovaUAuthError("CovaU rejected authentication or access")
                if response.status != 200:
                    raise CovaUError(f"CovaU returned HTTP {response.status}")
                try:
                    return await response.json(content_type=None)
                except ValueError:
                    raise CovaUDataError("CovaU returned invalid JSON") from None
        except (TimeoutError, aiohttp.ClientError):
            raise CovaUError("Unable to connect securely to CovaU") from None

    async def login(self) -> None:
        """Log in afresh, clearing any previously issued token."""
        self._token = self._party = None
        data = object_value(
            await self._request(
                "POST",
                "/security/login",
                authenticated=False,
                body={"OssLogin": self._account, "Password": self._password},
            ),
            "login",
        )
        token = data.get("accessToken")
        if not isinstance(token, str) or not token.strip():
            raise CovaUAuthError("Login did not return an access token")
        party = identifier(
            object_value(data.get("userBasicInfo"), "login userBasicInfo").get("seqPartyId"),
            "seqPartyId",
        )
        self._token, self._party = token, party

    async def accounts(self) -> list[dict[str, str]]:
        """Discover selectable services using the authenticated party."""
        data = rows_value(
            await self._request(
                "GET", "/lookup/customer-account", params={"SeqPartyID": self._party or ""}
            ),
            "accounts",
        )
        accounts = []
        seen: set[str] = set()
        for row in data:
            service_id = identifier(row.get("id"), "service id")
            if service_id in seen:
                raise CovaUDataError("Duplicate service identifier")
            seen.add(service_id)
            label = " - ".join(str(row[key]) for key in ("code", "text") if row.get(key))
            accounts.append({"id": service_id, "label": label or service_id})
        return accounts

    async def snapshot(self, service_id: str, today: date) -> dict[str, Any]:
        """Retrieve a coherent snapshot, without caching or persisting tokens."""
        await self.login()
        available = await self.accounts()
        if service_id not in {row["id"] for row in available}:
            raise CovaUAuthError("The selected service is no longer available to this account")
        usage = await self._request(
            "GET",
            "/usage/daily",
            params={
                "seqProductItemId": service_id,
                "StartDate": (today - timedelta(days=35)).isoformat(),
                "EndDate": (today - timedelta(days=1)).isoformat(),
                "SeqPartyID": self._party or "",
            },
        )
        rates = await self._request(
            "GET",
            "/wp-json/covau/v1/utility/rate-details",
            portal=True,
            params={"seqProductItemId": service_id},
        )
        customer = await self._request("GET", "/wp-json/covau/v1/Customer", portal=True)
        finance = await self._request("GET", "/wp-json/covau/v1/customer/finance", portal=True)
        return {
            "usage": rows_value(usage, "daily usage"),
            "pricings": rows_value(object_value(rates, "rate details").get("pricings"), "pricings"),
            "customer": object_value(
                object_value(customer, "Customer").get("customer"), "customer"
            ),
            "finance": object_value(finance, "finance"),
        }
