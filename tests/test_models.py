"""Regression tests against the demonstrated provider field shapes."""

from copy import deepcopy
from datetime import date, timedelta

import pytest
from covau_unit.api import CovaUDataError
from covau_unit.models import normalize, number, provider_date, stable_key

TODAY = date(2026, 10, 25)


def test_latest_day_not_latest_per_category(snapshot):
    values = normalize(snapshot, "123", TODAY)
    assert values["usage_date"].state == date(2026, 10, 24)
    assert values[stable_key("usage", "Off Peak")].state == 7.426
    assert values[stable_key("usage", "Off Peak - EV")].state is None
    assert values[stable_key("usage", "Standard FiT")].state == 13.398
    assert values[stable_key("usage", "Off Peak - Free")].unit == "kWh"
    assert values[stable_key("usage", "Off Peak")].attributes["reading_date"] == "2026-10-24"


def test_pricing_keeps_cents_zero_negatives_and_unknown_gst(snapshot):
    values = normalize(snapshot, "123", TODAY)
    assert values["pricing_count"].state == 4
    negative = values[stable_key("price", "Feed-in Peak - Step 1", "c/kWh", "unitRateIncGst")]
    assert negative.state == -15
    assert negative.unit == "c/kWh"
    assert negative.attributes["start_date"] == "2026-07-01"
    assert negative.attributes["rate_basis"] == "ex GST"
    assert stable_key("price", "Free Usage - Step 1", "c/kWh", "unitRate") not in values
    assert values[stable_key("price", "Free Usage - Step 1", "c/kWh", "unitRateIncGst")].state == 0
    assert (
        values[stable_key("price", "Daily Supply Charge", "c/Day", "unitRateIncGst")].state
        == 151.998
    )


@pytest.mark.parametrize(
    ("inclusive", "exclusive", "expected", "basis"),
    [
        ("", "0.00", 0.0, "ex GST"),
        (None, "0.00", 0.0, "ex GST"),
        ("   ", "0.00", 0.0, "ex GST"),
        ("0", "7", 0.0, "inc GST"),
        ("2", "7", 2.0, "inc GST"),
        ("", "", None, "unknown"),
        (None, "  ", None, "unknown"),
    ],
)
def test_free_usage_prefers_available_rate(snapshot, inclusive, exclusive, expected, basis):
    row = snapshot["pricings"][3]
    row.update(unitRateIncGst=inclusive, unitRate=exclusive)
    key = stable_key("price", row["usageDesc"], row["unitDesc"], "unitRateIncGst")
    value = normalize(snapshot, "123", TODAY)[key]
    assert value.state == expected
    assert value.attributes["rate_basis"] == basis


def test_invalid_free_usage_rate_is_not_silently_skipped(snapshot):
    snapshot["pricings"][3]["unitRateIncGst"] = "invalid"
    with pytest.raises(CovaUDataError, match="Invalid numeric value"):
        normalize(snapshot, "123", TODAY)


def test_missing_inc_gst_for_non_free_usage_remains_unknown(snapshot):
    row = snapshot["pricings"][1]
    row["unitRateIncGst"] = ""
    key = stable_key("price", row["usageDesc"], row["unitDesc"], "unitRateIncGst")
    assert normalize(snapshot, "123", TODAY)[key].state is None


@pytest.mark.parametrize(
    ("name", "rate", "expected"),
    [
        ("Feed-in Peak - Step 1", "-0.15", -0.15),
        ("Feed in Off Peak", "-0.05", -0.05),
        ("Standard FiT", "0", 0.0),
        ("Feed-in Peak - Step 1", "", None),
    ],
)
def test_feed_in_uses_provider_ex_gst_rate(snapshot, name, rate, expected):
    snapshot["pricings"][2].update(
        usageDesc=name, unitDesc="$/kWh", unitRate=rate, unitRateIncGst="-99"
    )
    key = stable_key("price", name, "$/kWh", "unitRateIncGst")
    value = normalize(snapshot, "123", TODAY)[key]
    assert value.state == expected
    assert value.attributes["rate_basis"] == "ex GST"
    assert "ex GST" in value.name


def test_feed_in_rate_is_not_guessed_when_inactive_or_invalid(snapshot):
    row = snapshot["pricings"][2]
    key = stable_key("price", row["usageDesc"], row["unitDesc"], "unitRateIncGst")
    row["startDate"] = "01/01/2027"
    assert normalize(snapshot, "123", TODAY)[key].state is None
    row.update(startDate="01/07/2026", unitRate="invalid")
    with pytest.raises(CovaUDataError, match="Invalid numeric value"):
        normalize(snapshot, "123", TODAY)


def test_privacy_and_billing_unknowns(snapshot):
    values = normalize(snapshot, "123", TODAY)
    for key, value in values.items():
        if key.startswith("customer_"):
            assert value.enabled is False
    assert values["customer_accountNo"].state == "0000123"
    assert values["finance_totalBalance"].state == 0
    assert values["finance_totalBalance"].unit == "AUD"
    assert values["finance_lastPayAmt"].state is None
    for key in ("invoiceDate", "invDueDate", "lastPayDate", "nextReadDate"):
        assert values[f"finance_{key}"].state is None


@pytest.mark.parametrize("key", ["invoiceDate", "invDueDate", "lastPayDate", "nextReadDate"])
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("11/27/2026 00:00:00", date(2026, 11, 27)),
        ("11/10/2026 00:00:00", date(2026, 11, 10)),
        ("Unknown", None),
        (None, None),
        ("2026-11-10T00:00:00Z", date(2026, 11, 10)),
    ],
)
def test_finance_dates_are_month_first(snapshot, key, value, expected):
    snapshot["finance"][key] = value
    assert normalize(snapshot, "123", TODAY)[f"finance_{key}"].state == expected


def test_period_selection_and_identity_are_stable(snapshot):
    old = deepcopy(snapshot["pricings"][1])
    old.update(unitRateIncGst="10", startDate="01/01/2026", endDate="30/06/2026")
    future = deepcopy(old)
    future.update(unitRateIncGst="99", startDate="01/01/2027", endDate="")
    before = normalize(snapshot, "123", TODAY)
    snapshot["pricings"].extend([old, future])
    after = normalize(snapshot, "123", TODAY)
    key = stable_key("price", "Off Peak Usage - Step 1", "c/kWh", "unitRateIncGst")
    assert before.keys() == after.keys()
    assert after[key].state == 26.906
    # With only a future tariff, expose unknown rather than yesterday's or future price.
    snapshot["pricings"] = [future]
    assert normalize(snapshot, "123", TODAY)[key].state is None


def test_ambiguous_period_rejected(snapshot):
    snapshot["pricings"].append(deepcopy(snapshot["pricings"][0]))
    with pytest.raises(CovaUDataError, match="Ambiguous"):
        normalize(snapshot, "123", TODAY)


def test_no_readings_is_unknown_not_zero(snapshot):
    snapshot["usage"] = []
    values = normalize(snapshot, "123", TODAY)
    assert values["usage_date"].state is None
    assert not any(key.startswith("usage_") and key != "usage_date" for key in values)


def test_units_are_not_assumed(snapshot):
    snapshot["pricings"] = []
    values = normalize(snapshot, "123", TODAY)
    assert values[stable_key("usage", "Off Peak")].unit is None


@pytest.mark.parametrize(
    "change",
    [
        {"seqProductItemId": 999},
        {"readValue": "not a number"},
        {"readDatetime": ""},
        {"readDatetime": "10/25/2026"},
        {"category": ""},
    ],
)
def test_bad_usage_is_explicit_error(snapshot, change):
    snapshot["usage"][0].update(change)
    with pytest.raises(CovaUDataError):
        normalize(snapshot, "123", TODAY)


def test_duplicate_reading_is_not_silently_overwritten(snapshot):
    snapshot["usage"].append(deepcopy(snapshot["usage"][0]))
    with pytest.raises(CovaUDataError, match="Duplicate"):
        normalize(snapshot, "123", TODAY)


def test_history_bounded(snapshot):
    snapshot["usage"] = [
        {
            "seqProductItemId": 123,
            "category": "Example",
            "readDatetime": (TODAY - timedelta(days=i)).isoformat(),
            "readValue": i,
        }
        for i in range(1, 70)
    ]
    value = normalize(snapshot, "123", TODAY)[stable_key("usage", "Example")]
    assert len(value.attributes["history"]) == 35
    assert value.state == 1


@pytest.mark.parametrize("value", [True, "abc", "NaN", "Infinity", "1e999"])
def test_invalid_numbers(value):
    with pytest.raises(CovaUDataError):
        number(value)


@pytest.mark.parametrize("value", ["", None, "Unknown", "N/A"])
def test_unknown_dates(value):
    assert provider_date(value) is None


def test_date_formats():
    assert provider_date("10/23/2026 00:00:00", month_first=True) == date(2026, 10, 23)
    assert provider_date("01/07/2026") == date(2026, 7, 1)
    assert provider_date("2026-10-23T00:00:00Z") == date(2026, 10, 23)
    assert provider_date("2026-10-23T00:00:00Z", month_first=True) == date(2026, 10, 23)
    with pytest.raises(CovaUDataError):
        provider_date("not a date")
    with pytest.raises(CovaUDataError, match="^Unsupported provider date format$"):
        provider_date("27/11/2026", month_first=True)
