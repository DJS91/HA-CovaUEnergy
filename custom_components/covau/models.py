"""Normalize provider data without guessing missing readings or GST."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Literal

from .api import CovaUDataError, identifier

State = str | float | int | date | None
DeviceClass = Literal["date", "energy", "monetary"]


@dataclass(frozen=True)
class Value:
    """One sensor's identity, state and non-secret metadata."""

    name: str
    state: State
    unit: str | None = None
    device_class: DeviceClass | None = None
    enabled: bool = True
    attributes: dict[str, Any] = field(default_factory=dict)


def number(value: Any) -> float | None:
    """Preserve zero and negative values, rejecting malformed numbers."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise CovaUDataError("Invalid numeric value")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise CovaUDataError("Invalid numeric value") from None
    if not result.is_finite() or not float("-inf") < float(result) < float("inf"):
        raise CovaUDataError("Non-finite numeric value")
    return float(result)


def provider_date(value: Any, *, usage: bool = False) -> date | None:
    """Usage is month-first; portal rate/billing dates are day-first."""
    if value is None or value == "" or str(value).strip().lower() in {"unknown", "n/a"}:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    formats = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y") if usage else ("%d/%m/%Y", "%d/%m/%Y %H:%M:%S")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CovaUDataError("Unsupported provider date format")


def text(value: Any) -> str | None:
    """Return empty scalar fields as unknown."""
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list, bool)):
        raise CovaUDataError("Expected a scalar text field")
    result = str(value)
    if len(result) > 255:
        raise CovaUDataError("Text field exceeds the Home Assistant state limit")
    return result


def stable_key(prefix: str, *parts: str) -> str:
    """Avoid collisions caused by slugging provider tariff/category labels."""
    digest = sha256("\x00".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


CUSTOMER_FIELDS = {
    "accountNo": "Account number",
    "customerName": "Customer name",
    "postalAddress": "Postal address",
    "phoneStd": "Phone area code",
    "phoneNo": "Phone number",
    "mobileStd": "Mobile area code",
    "mobileNo": "Mobile number",
    "emailAddress": "Email address",
    "address": "Street address",
    "suburb": "Suburb",
    "state": "State",
    "postCode": "Postcode",
}
FINANCE_MONEY = {
    "totalBalance": "Total balance",
    "amountDue": "Amount due",
    "overdueAmount": "Overdue amount",
    "lastPayAmt": "Last payment amount",
}
FINANCE_DATES = {
    "invoiceDate": "Invoice date",
    "invDueDate": "Invoice due date",
    "lastPayDate": "Last payment date",
    "nextReadDate": "Next read date",
}
FINANCE_TEXT = {
    "partyCode": "Billing account number",
    "invoiceNumber": "Invoice number",
    "paymentMethod": "Payment method code",
    "paymentMethodDesc": "Payment method",
    "billCycleDesc": "Billing cycle",
    "totalCustomerOffering": "Customer offering",
}


def normalize(snapshot: dict[str, Any], service_id: str, today: date) -> dict[str, Value]:
    """Build the latest-day sensors and effective tariff sensors."""
    result: dict[str, Value] = {}
    readings: dict[str, dict[date, float | None]] = {}
    for row in snapshot["usage"]:
        if identifier(row.get("seqProductItemId"), "usage service id") != service_id:
            raise CovaUDataError("Usage response contains another service")
        category = identifier(row.get("category"), "usage category")
        day = provider_date(row.get("readDatetime"), usage=True)
        if day is None:
            raise CovaUDataError("Usage reading has no date")
        if day >= today:
            raise CovaUDataError("Usage response contains an incomplete or future day")
        days = readings.setdefault(category, {})
        if day in days:
            raise CovaUDataError("Duplicate daily usage category/date")
        days[day] = number(row.get("readValue"))

    latest = max((day for days in readings.values() for day in days), default=None)
    result["usage_date"] = Value("Daily usage date", latest, device_class="date")

    tariffs: dict[tuple[str, str], list[tuple[date | None, date | None, dict[str, Any]]]] = {}
    for row in snapshot["pricings"]:
        description = identifier(row.get("usageDesc"), "tariff description")
        unit = identifier(row.get("unitDesc"), "tariff unit")
        start, end = provider_date(row.get("startDate")), provider_date(row.get("endDate"))
        if start and end and start > end:
            raise CovaUDataError("Tariff end date precedes its start date")
        tariffs.setdefault((description, unit), []).append((start, end, row))

    energy_units = {
        unit.split("/", 1)[1] for _, unit in tariffs if unit in {"c/kWh", "c/MJ", "$/kWh", "$/MJ"}
    }
    usage_unit = next(iter(energy_units)) if len(energy_units) == 1 else None
    for category, days in readings.items():
        result[stable_key("usage", category)] = Value(
            f"Daily usage {category}",
            days.get(latest),
            unit=usage_unit,
            device_class="energy" if usage_unit else None,
            attributes={
                "reading_date": latest.isoformat() if latest else None,
                "category": category,
                "history": [
                    {"date": day.isoformat(), "value": value}
                    for day, value in sorted(days.items())[-35:]
                ],
            },
        )

    active_count = 0
    for (description, unit), periods in tariffs.items():
        active = [
            (start, end, row)
            for start, end, row in periods
            if (not start or start <= today) and (not end or today <= end)
        ]
        active.sort(key=lambda item: item[0] or date.min, reverse=True)
        if len(active) > 1 and active[0][0] == active[1][0]:
            raise CovaUDataError("Ambiguous tariff periods with the same start date")
        chosen = active[0] if active else None
        if chosen:
            active_count += 1
        attrs = {
            "tariff": description,
            "start_date": chosen[0].isoformat() if chosen and chosen[0] else None,
            "end_date": chosen[1].isoformat() if chosen and chosen[1] else None,
        }
        for field_name, label in (("unitRate", "ex GST"), ("unitRateIncGst", "inc GST")):
            result[stable_key("price", description, unit, field_name)] = Value(
                f"Price {description} {label}",
                number(chosen[2].get(field_name)) if chosen else None,
                unit=unit,
                attributes=attrs,
            )
    result["pricing_count"] = Value("Active pricings", active_count)

    customer = snapshot["customer"]
    for key, label in CUSTOMER_FIELDS.items():
        result[f"customer_{key}"] = Value(label, text(customer.get(key)), enabled=False)
    finance = snapshot["finance"]
    for key, label in FINANCE_MONEY.items():
        result[f"finance_{key}"] = Value(
            label, number(finance.get(key)), unit="AUD", device_class="monetary"
        )
    for key, label in FINANCE_DATES.items():
        result[f"finance_{key}"] = Value(
            label, provider_date(finance.get(key)), device_class="date"
        )
    for key, label in FINANCE_TEXT.items():
        result[f"finance_{key}"] = Value(
            label, text(finance.get(key)), enabled=key not in {"partyCode", "invoiceNumber"}
        )
    return result
