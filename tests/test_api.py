"""Exercise real aiohttp requests against local, synthetic HTTP servers."""

from datetime import date
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from covau_unit import api
from covau_unit.api import CovaUAuthError, CovaUClient, CovaUDataError, CovaUError

LOGIN = {
    "accessToken": "synthetic-token",
    "refreshToken": "synthetic-refresh",
    "userBasicInfo": {"seqPartyId": 456},
}
LOGIN_PATH = "/api/v1/security/login"
LOOKUP = "/api/v1/lookup/customer-account"
USAGE = "/api/v1/usage/daily"
RATES = "/wp-json/covau/v1/utility/rate-details"
CUSTOMER = "/wp-json/covau/v1/Customer"
FINANCE = "/wp-json/covau/v1/customer/finance"
TODAY = date(2026, 10, 25)


@pytest.fixture
async def servers(monkeypatch, snapshot):
    routes = {
        LOGIN_PATH: (200, LOGIN),
        LOOKUP: (200, [{"id": 123, "code": "Example", "text": "Electricity"}]),
        USAGE: (200, snapshot["usage"]),
        RATES: (200, {"pricings": snapshot["pricings"]}),
        CUSTOMER: (200, {"customer": snapshot["customer"]}),
        FINANCE: (200, snapshot["finance"]),
    }
    calls = []

    async def handle(request):
        calls.append(
            {
                "method": request.method,
                "path": request.path,
                "host": request.host,
                "query": dict(request.query),
                "headers": dict(request.headers),
                "body": await request.json() if request.can_read_body else None,
            }
        )
        status, payload = routes[request.path]
        if isinstance(payload, str):
            response = web.Response(text=payload, status=status)
        else:
            response = web.json_response(payload, status=status)
        if status == 302:
            response.headers["Location"] = "/redirect-target"
        if request.path.startswith("/wp-json"):
            response.set_cookie("provider_tracking", "synthetic")
        return response

    mobile_app, portal_app = web.Application(), web.Application()
    mobile_app.router.add_route("*", "/{tail:.*}", handle)
    portal_app.router.add_route("*", "/{tail:.*}", handle)
    async with TestServer(mobile_app) as mobile, TestServer(portal_app) as portal:
        monkeypatch.setattr(api, "API_BASE", str(mobile.make_url("/api/v1")))
        monkeypatch.setattr(api, "PORTAL_BASE", str(portal.make_url("/")).rstrip("/"))
        yield routes, calls


async def test_exact_requests_and_cookie_isolation(servers, snapshot):
    _, calls = servers
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        result = await CovaUClient(session, "0000123", "synthetic-password").snapshot("123", TODAY)
        assert result == snapshot
        assert len(calls) == 6
        assert calls[0]["method"] == "POST"
        assert calls[0]["body"] == {"OssLogin": "0000123", "Password": "synthetic-password"}
        assert calls[1]["query"] == {"SeqPartyID": "456"}
        assert calls[2]["query"] == {
            "seqProductItemId": "123",
            "StartDate": "2026-09-20",
            "EndDate": "2026-10-24",
            "SeqPartyID": "456",
        }
        assert calls[3]["query"] == {"seqProductItemId": "123"}
        assert calls[4]["path"] == CUSTOMER
        assert calls[5]["path"] == FINANCE
        assert not calls[4]["query"] and not calls[5]["query"]
        for call in calls:
            headers = call["headers"]
            if call["method"] == "POST":
                assert "Authorization" not in headers and "Cookie" not in headers
            else:
                assert call["method"] == "GET" and call["body"] is None
                if call["path"].startswith("/api"):
                    assert headers["Authorization"] == "Bearer synthetic-token"
                    assert "Cookie" not in headers
                else:
                    assert headers["Cookie"] == "covauToken=synthetic-token; seqPartyId=456"
                    assert "Authorization" not in headers
                    assert call["host"] != calls[0]["host"]
        assert not list(session.cookie_jar)
        assert "synthetic-token" not in str(result)
        assert "synthetic-password" not in str(result)


@pytest.mark.parametrize("status", [401, 403])
async def test_login_auth_rejection(servers, status):
    routes, _ = servers
    routes[LOGIN_PATH] = status, {}
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUAuthError):
            await CovaUClient(session, "example", "secret").login()


@pytest.mark.parametrize("status", [302, 429, 500])
async def test_bad_http_status_not_success(servers, status):
    routes, _ = servers
    routes[LOGIN_PATH] = status, {}
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUError, match=f"HTTP {status}"):
            await CovaUClient(session, "example", "secret").login()


@pytest.mark.parametrize("error", [TimeoutError(), aiohttp.ClientConnectionError()])
async def test_connection_errors_do_not_expose_secrets(error):
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "request", side_effect=error):
            with pytest.raises(CovaUError, match="Unable to connect securely") as caught:
                await CovaUClient(session, "example", "secret").login()
            assert "secret" not in str(caught.value)


async def test_invalid_json(servers):
    routes, _ = servers
    routes[LOGIN_PATH] = 200, "<html>not json</html>"
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUDataError, match="invalid JSON"):
            await CovaUClient(session, "example", "secret").login()


@pytest.mark.parametrize("login", [{}, {"accessToken": ""}, {"accessToken": "x"}])
async def test_malformed_login(servers, login):
    routes, _ = servers
    routes[LOGIN_PATH] = 200, login
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUError):
            await CovaUClient(session, "example", "secret").login()


async def test_wrong_service_never_requested(servers):
    routes, calls = servers
    routes[LOOKUP] = 200, [{"id": 999}]
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUAuthError, match="no longer available"):
            await CovaUClient(session, "example", "secret").snapshot("123", TODAY)
        assert len(calls) == 2


async def test_daily_refresh_logs_in_again(servers):
    _, calls = servers
    async with aiohttp.ClientSession() as session:
        client = CovaUClient(session, "example", "secret")
        await client.snapshot("123", TODAY)
        await client.snapshot("123", TODAY)
        assert len([call for call in calls if call["path"] == LOGIN_PATH]) == 2


async def test_endpoint_failure_does_not_return_partial_data(servers):
    routes, _ = servers
    routes[RATES] = 503, {}
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUError, match="HTTP 503"):
            await CovaUClient(session, "example", "secret").snapshot("123", TODAY)


@pytest.mark.parametrize("path", [USAGE, RATES, CUSTOMER, FINANCE])
async def test_authenticated_endpoint_rejection(servers, path):
    routes, _ = servers
    routes[path] = 403, {}
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        with pytest.raises(CovaUAuthError):
            await CovaUClient(session, "example", "secret").snapshot("123", TODAY)


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (USAGE, {}),
        (USAGE, ["not an object"]),
        (RATES, {"pricings": {}}),
        (CUSTOMER, {"customer": []}),
        (FINANCE, []),
        (LOOKUP, [{"id": ""}]),
        (LOOKUP, [{"id": 123}, {"id": 123}]),
    ],
)
async def test_unsupported_response_shape_is_visible(servers, path, payload):
    routes, _ = servers
    routes[path] = 200, payload
    async with aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()) as session:
        with pytest.raises(CovaUDataError):
            await CovaUClient(session, "example", "secret").snapshot("123", TODAY)


async def test_redirect_is_not_followed(servers):
    routes, calls = servers
    routes[LOGIN_PATH] = 302, {}
    # Even an auth redirect must surface as an error, not transfer credentials.
    async with aiohttp.ClientSession() as session:
        with pytest.raises(CovaUError, match="HTTP 302"):
            await CovaUClient(session, "example", "secret").login()
    assert len(calls) == 1


async def test_request_options():
    async with aiohttp.ClientSession() as session:
        with patch.object(session, "request", side_effect=TimeoutError()) as request:
            with pytest.raises(CovaUError):
                await CovaUClient(session, "example", "secret").login()
            options = request.call_args.kwargs
            assert options["allow_redirects"] is False
            assert options["timeout"].total == 30
            assert "ssl" not in options  # aiohttp default verifies certificates.
