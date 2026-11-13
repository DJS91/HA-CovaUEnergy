"""Cost estimates from synthetic usage and pricing."""

from datetime import date

import pytest
from covau_unit.api import CovaUDataError
from covau_unit.cost import calculate_costs, cycle_start
from covau_unit.models import Value, normalize, stable_key

TODAY = date(2026, 10, 25)
USAGE = stable_key("usage", "Off Peak")
FEED = stable_key("usage", "Standard FiT")
RATE = stable_key("price", "Off Peak Usage - Step 1", "c/kWh", "unitRateIncGst")
FEED_RATE = stable_key("price", "Feed-in Peak - Step 1", "c/kWh", "unitRateIncGst")
SUPPLY = stable_key("price", "Daily Supply Charge", "c/Day", "unitRateIncGst")


@pytest.mark.parametrize(
    ("today", "day", "expected"),
    [
        (date(2026, 3, 1), 31, date(2026, 2, 28)),
        (date(2024, 2, 29), 31, date(2024, 2, 29)),
        (date(2026, 1, 1), 15, date(2025, 12, 15)),
        (date(2026, 10, 25), 25, date(2026, 10, 25)),
    ],
)
def test_cycle_start(today, day, expected):
    assert cycle_start(today, day) == expected


@pytest.mark.parametrize("day", [0, 32, True, 1.5, None])
def test_invalid_cycle_start(day):
    with pytest.raises(CovaUDataError, match="billing cycle"):
        cycle_start(TODAY, day)


def test_estimated_costs_include_negative_feed_in_and_supply(snapshot):
    snapshot["pricings"][2]["unitRateIncGst"] = "-15"
    values = normalize(snapshot, "123", TODAY)
    config = {"billing_day": 23, "mappings": {USAGE: RATE, FEED: FEED_RATE}, "supply": [SUPPLY]}
    costs = calculate_costs(values, config, TODAY)
    assert costs[f"cost_{RATE}"].state == pytest.approx((0.004 + 7.426) * 0.26906)
    assert costs[f"cost_{FEED_RATE}"].state == pytest.approx(13.398 * -0.15)
    assert costs[f"cost_{SUPPLY}"].state == pytest.approx(3 * 1.51998)
    assert costs["cost_total"].state == pytest.approx(
        (0.004 + 7.426) * 0.26906 - 13.398 * 0.15 + 3 * 1.51998
    )
    assert costs[f"cost_{FEED_RATE}"].attributes["missing_readings"] == 1
    assert costs["cost_total"].attributes["partial"] is True
    assert costs[f"cost_{SUPPLY}"].attributes["supply_through"] == "2026-10-25"


def test_missing_rate_or_usage_is_not_assumed_zero(snapshot):
    values = normalize(snapshot, "123", TODAY)
    config = {"billing_day": 23, "mappings": {FEED: FEED_RATE}, "supply": [SUPPLY]}
    costs = calculate_costs(values, config, TODAY)
    assert costs[f"cost_{FEED_RATE}"].state is None
    assert costs[f"cost_{FEED_RATE}"].attributes["rate_available"] is False
    assert costs["cost_total"].state == pytest.approx(3 * 1.51998)
    assert costs["cost_total"].attributes["partial"] is True
    config["mappings"] = {stable_key("usage", "Missing"): RATE}
    assert calculate_costs(values, config, TODAY)[f"cost_{RATE}"].state is None


def test_new_cycle_resets_usage_but_charges_today_supply(snapshot):
    values = normalize(snapshot, "123", TODAY)
    costs = calculate_costs(
        values, {"billing_day": 25, "mappings": {USAGE: RATE}, "supply": [SUPPLY]}, TODAY
    )
    assert costs[f"cost_{RATE}"].state == 0
    assert costs["cost_total"].state == pytest.approx(1.51998)
    assert costs["cost_total"].attributes["partial"] is False


def test_incompatible_usage_unit_is_not_calculated(snapshot):
    snapshot["pricings"].append(
        {"usageDesc": "Gas", "unitDesc": "c/MJ", "unitRateIncGst": "1", "startDate": ""}
    )
    values = normalize(snapshot, "123", TODAY)
    costs = calculate_costs(
        values, {"billing_day": 23, "mappings": {USAGE: RATE}, "supply": []}, TODAY
    )
    assert costs[f"cost_{RATE}"].state is None
    assert costs["cost_total"].attributes["partial"] is True


def test_multiple_usage_categories_sum_under_one_tariff(snapshot):
    values = normalize(snapshot, "123", TODAY)
    costs = calculate_costs(
        values, {"billing_day": 24, "mappings": {USAGE: RATE, FEED: RATE}, "supply": []}, TODAY
    )
    assert costs[f"cost_{RATE}"].state == pytest.approx((7.426 + 13.398) * 0.26906)
    assert costs["cost_total"].attributes["partial"] is False


def test_unavailable_price_and_partial_usage_are_explicit(snapshot):
    values = normalize(snapshot, "123", TODAY)
    costs = calculate_costs(
        values,
        {"billing_day": 22, "mappings": {USAGE: RATE, FEED: "price_missing"}, "supply": []},
        TODAY,
    )
    assert costs[f"cost_{RATE}"].attributes["missing_readings"] == 1
    assert costs["cost_price_missing"].state is None
    assert costs["cost_total"].state == pytest.approx((0.004 + 7.426) * 0.26906)
    assert costs["cost_total"].attributes["partial"] is True


def test_current_dollar_rate_reprices_the_whole_cycle(snapshot):
    snapshot["pricings"][1].update(unitDesc="$/kWh", unitRateIncGst="0.35")
    values = normalize(snapshot, "123", TODAY)
    rate = stable_key("price", "Off Peak Usage - Step 1", "$/kWh", "unitRateIncGst")
    costs = calculate_costs(
        values, {"billing_day": 23, "mappings": {USAGE: rate}, "supply": []}, TODAY
    )
    assert costs[f"cost_{rate}"].state == pytest.approx((0.004 + 7.426) * 0.35)


def test_ex_gst_price_is_never_used(snapshot):
    values = normalize(snapshot, "123", TODAY)
    ex_rate = stable_key("price", "Off Peak Usage - Step 1", "c/kWh", "unitRate")
    values[ex_rate] = Value(
        "Price Off Peak Usage - Step 1 ex GST",
        24.46,
        "c/kWh",
        attributes={"tariff": "Off Peak Usage - Step 1"},
    )
    costs = calculate_costs(
        values, {"billing_day": 23, "mappings": {USAGE: ex_rate}, "supply": []}, TODAY
    )
    assert costs[f"cost_{ex_rate}"].state is None
    assert costs["cost_total"].attributes["partial"] is True


def test_zero_rate_is_a_known_zero_cost(snapshot):
    snapshot["pricings"][1]["unitRateIncGst"] = "0"
    values = normalize(snapshot, "123", TODAY)
    costs = calculate_costs(
        values, {"billing_day": 24, "mappings": {USAGE: RATE}, "supply": []}, TODAY
    )
    assert costs["cost_total"].state == 0
    assert costs["cost_total"].attributes["partial"] is False
