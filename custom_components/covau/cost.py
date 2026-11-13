"""Estimated billing-cycle costs from explicitly selected usage and prices."""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any

from .api import CovaUDataError
from .models import Value, stable_key


def _cost_state(amount: Decimal) -> float:
    result = float(amount)
    if not isfinite(result):
        raise CovaUDataError("Non-finite estimated cost")
    return result


def cycle_start(today: date, day: int) -> date:
    """Return the latest monthly reset date, clamped to the end of the month."""
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 31:
        raise CovaUDataError("Invalid billing cycle start day")
    current = date(today.year, today.month, min(day, monthrange(today.year, today.month)[1]))
    if current <= today:
        return current
    previous = current - timedelta(days=current.day)
    return date(previous.year, previous.month, min(day, previous.day))


def calculate_costs(
    values: dict[str, Value], config: dict[str, Any], today: date
) -> dict[str, Value]:
    """Estimate mapped costs in AUD using today's GST-inclusive prices."""
    if not isinstance(config, dict):
        raise CovaUDataError("Invalid cost configuration")
    if not config:
        return {}
    start = cycle_start(today, config.get("billing_day"))
    mappings = config.get("mappings")
    supply = config.get("supply")
    if (
        not isinstance(mappings, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in mappings.items())
        or not isinstance(supply, list)
        or not all(isinstance(key, str) for key in supply)
        or len(set(supply)) != len(supply)
    ):
        raise CovaUDataError("Invalid cost configuration")

    grouped: dict[str, list[str]] = {}
    for usage_key, price_key in mappings.items():
        grouped.setdefault(price_key, []).append(usage_key)
    if set(grouped) & set(supply):
        raise CovaUDataError("Invalid cost configuration")

    result: dict[str, Value] = {}
    total = Decimal(0)
    has_total = False
    total_partial = False
    expected_days = (today - start).days
    for price_key in sorted(set(grouped) | set(supply)):
        price = values.get(price_key)
        rate = price.state if price else None
        is_supply = price_key in supply
        units = {"c/Day", "$/Day"} if is_supply else {"c/kWh", "$/kWh", "c/MJ", "$/MJ"}
        valid_unit = price is not None and price.unit in units
        rate_known = valid_unit and isinstance(rate, (int, float)) and not isinstance(rate, bool)
        if rate_known and price is not None and price.unit is not None:
            tariff = price.attributes.get("tariff")
            rate_known = isinstance(tariff, str) and price_key == stable_key(
                "price", tariff, price.unit, "unitRateIncGst"
            )
        amount = Decimal(0)
        has_amount = False
        missing = 0
        partial = not rate_known
        if rate_known and price is not None and price.unit is not None:
            multiplier = Decimal(str(rate)) / (100 if price.unit.startswith("c/") else 1)
            if is_supply:
                amount = multiplier * (expected_days + 1)
                has_amount = True
            else:
                for usage_key in grouped[price_key]:
                    usage = values.get(usage_key)
                    if usage is None or usage.unit != price.unit.split("/", 1)[1]:
                        missing += expected_days
                        partial = True
                        continue
                    history = usage.attributes["history"]
                    readings = [
                        row["value"]
                        for row in history
                        if start <= date.fromisoformat(row["date"]) < today
                        and row["value"] is not None
                    ]
                    missing += expected_days - len(readings)
                    if readings or expected_days == 0:
                        amount += (
                            sum((Decimal(str(value)) for value in readings), Decimal(0))
                            * multiplier
                        )
                        has_amount = True
        partial |= missing > 0
        if has_amount:
            total += amount
            has_total = True
        total_partial |= partial
        description = price.attributes.get("tariff", price_key) if price else price_key
        result[f"cost_{price_key}"] = Value(
            f"Current cycle cost {description}",
            _cost_state(amount) if has_amount else None,
            unit="AUD",
            device_class="monetary",
            attributes={
                "cycle_start": start.isoformat(),
                "partial": partial,
                "missing_readings": missing,
                "rate_available": bool(rate_known),
                "rate_basis": "current inc GST",
                "supply_through": today.isoformat() if is_supply else None,
            },
        )

    if grouped or supply:
        result["cost_total"] = Value(
            "Current cycle cost total",
            _cost_state(total) if has_total else None,
            unit="AUD",
            device_class="monetary",
            attributes={
                "cycle_start": start.isoformat(),
                "partial": total_partial,
                "rate_basis": "current inc GST",
            },
        )
    return result
