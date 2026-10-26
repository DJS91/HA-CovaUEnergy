# CovaU for Home Assistant

Unofficial, read-only custom integration using the CovaU mobile login and My Account
portal endpoints. It is not affiliated with or supported by CovaU.

## Install

Target: **Home Assistant 2026.10 or newer**. The configuration flow uses that
release's `probatio` schema API. Older releases have not been validated.

1. Copy the complete `custom_components/covau` directory into your Home Assistant
   configuration directory, retaining this structure:

   ```text
   config/
     custom_components/
       covau/
         __init__.py
         manifest.json
         ...
         translations/
           en.json
   ```

   Alternatively, extract the supplied integration ZIP into the configuration
   directory; it contains `custom_components/covau`.
2. Restart Home Assistant.
3. Open **Settings > Devices & services > Add integration > CovaU**.
4. Enter your **account number** and **password**.
5. Select the relevant energy account/service from the authenticated account list.

Add the integration again for another service. The internal service identifier
(`seqProductItemId`) is taken from the selected lookup entry, not entered manually.
No YAML configuration, API key or copied browser cookies are required.

## Refresh schedule

All four sections refresh together:

- Immediately when the integration loads, including after a Home Assistant restart.
- Every day at **05:00 in Home Assistant's configured local timezone**.
- On a manual refresh through `homeassistant.update_entity` for an enabled CovaU sensor.

This uses a local wall-clock listener rather than a 24-hour polling interval, so
Home Assistant handles timezone and daylight-saving changes. The listener is removed
when the entry unloads. If Home Assistant is off at 05:00, the startup refresh retrieves
the data when it starts again. Provider reporting can lag; refreshing does not make
unpublished readings available.

Each refresh logs in again, verifies the selected service is still accessible, then
fetches daily usage, pricing, customer and finance. A failed section fails the whole
refresh instead of publishing a mixture of fresh and stale sections. Home Assistant
marks the entities unavailable and reports update errors; it requests reauthentication
for rejected authentication/access. The next scheduled or manual update retries.

## Sensors

### Daily usage

- **Daily usage date**: newest date returned within the 35-day lookback, ending yesterday.
- **Daily usage CATEGORY**: one sensor for every distinct category, for example Off Peak,
  Off Peak - EV, Off Peak - Free, Peak and Standard FiT.
- Each category exposes `reading_date`, `category` and up to 35 daily `history` entries
  as attributes.

All current states use the **same newest date**. If a category has no reading on that
date, its state is **unknown**, not zero or a reading from an earlier day. An empty
usage result also means unknown. New categories automatically get entities.

Electricity readings use kWh only when the pricing units unambiguously identify kWh.
MJ is supported similarly; otherwise usage units are left unspecified. Solar exports
remain separate from imports. There are deliberately no guessed import totals, calculated
bills, or `total_increasing` statistics.

These historical daily readings are **not cumulative meters** and are not directly
suitable as Home Assistant Energy dashboard consumption sources. No energy state class
is assigned. The integration does not backfill historical recorder statistics.
Recording the history attributes can increase database size.

### Pricings

- **Active pricings**: number of distinct currently effective tariff description/unit pairs.
- **Price DESCRIPTION ex GST** and **Price DESCRIPTION inc GST** per tariff.
- Units are retained exactly as supplied: for example `c/Day` and `c/kWh`, not dollars.
- Zero/free and negative/feed-in prices are retained.
- Blank inc-GST values stay unknown; GST is never calculated or assumed.
- Attributes include the tariff description and effective start/end dates.

For successive tariff periods with the same description/unit, the most recently
started currently effective period is selected. Non-effective tariffs are unknown.
Duplicate periods with the same effective start date are rejected as ambiguous.
Tariff identity does not include its amount or effective dates, so price changes
do not create replacement entities. New tariff descriptions/units get new entities.
The integration does not attempt to map daily categories to tariff tiers.

### Customer

Separate account number, name, postal address, phone/mobile components, email,
street address, suburb, state and postcode sensors.

**All customer sensors are disabled by default**, as are billing account/invoice
identifiers. Enable only what you need from the integration's entity list.
Disabled entities may be hidden by the default entity-list filter.

### Finance / billing

- Total balance, amount due, overdue amount and last payment amount, in AUD.
- Invoice date, invoice due date, last payment date and next read date.
- Invoice number, billing account number, payment method code/description, billing
  cycle and customer offering.

Blank amounts/dates and `Unknown` dates remain unknown. Zero balances remain zero.
Portal slash dates are interpreted as Australian day/month/year; daily-usage slash
timestamps are month/day/year, matching the supplied usage output. ISO dates and
timestamps are also accepted. Unexpected nonempty date formats cause an explicit
update error instead of guessing.

Customer and finance endpoints are **customer-level**, with no service parameter.
Their values may be repeated across multiple selected services for the same login;
they must not be added together as if they were service-specific balances.

## Privacy and security

- Home Assistant stores the account number and password in its config entry.
  Protect the configuration directory and backups; do not publish `.storage`.
- Access tokens remain in memory; no refresh token or browser tracking cookies are used.
- Dedicated cookie-less HTTP sessions prevent portal cookies from entering Home
  Assistant's shared cookie jar. The access cookie is sent only on portal requests.
- HTTPS certificate verification remains enabled; redirects are not followed.
- No credentials, tokens, raw HTTP response bodies or customer details are logged by
  this integration. Entity/device names can contain the provider's service labels.
- If personal entities are enabled, their states can enter Home Assistant history,
  backups and dashboards. Configure recorder exclusions if needed.
- This integration has no diagnostics download or account-changing actions.

## Connectivity and limitations

Outbound HTTPS must reach:

- `secure.covau.com.au:3011` for login, service lookup and usage.
- `www.myaccount.covau.com.au:443` for portal pricing/customer/finance.

If port 3011 is blocked, check your firewall/network. A certificate-chain error
requires correcting the trusted certificate chain or contacting the provider;
do not disable TLS verification. Home Assistant does not fix provider-side
connectivity or certificate problems.

These are undocumented endpoints reverse-engineered from the app and the supplied
working portal requests. Their availability and response formats can change.
The integration rejects unsupported JSON shapes, invalid numbers, duplicate daily
category/date rows, and readings for another service rather than silently
misrepresenting them.

## Development and validation

The API/data unit tests run on Python 3.12+ and use synthetic data with local HTTP
servers, never live customer credentials:

```text
uv sync --group test
uv run --group test pytest -q
uv run --group test ruff check .
uv run --group test ruff format --check .
```

To also run the HA-specific unit tests in a supported **Python 3.14.2+ Linux
environment**:

```text
uv sync --group test --group homeassistant-test
uv run --group test --group homeassistant-test pytest -q
```

Without the optional Home Assistant dependency, those tests are explicitly skipped;
no fake Home Assistant package is substituted. They cover configuration-flow logic,
reauthentication, setup/unload calls, sensor identity/privacy and the 05:00 timer contract.
They are unit tests, not a full Home Assistant installation or DST simulation.

Validation performed during development: API/data tests and Python syntax/lint.
The current Home Assistant source APIs were inspected, but an actual Home Assistant
instance and live authenticated provider calls were **not** run in the development
environment. Validate the first refresh and entity states in your installation
before relying on the data.
