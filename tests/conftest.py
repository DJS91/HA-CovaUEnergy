"""Load the HA-independent modules without importing the HA entry point."""

import sys
from pathlib import Path
from types import ModuleType

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "covau"
package = ModuleType("covau_unit")
package.__path__ = [str(PACKAGE)]
sys.modules["covau_unit"] = package


@pytest.fixture
def snapshot():
    """Synthetic values only; no real customer identifiers or contact details."""
    return {
        "usage": [
            {
                "seqProductItemId": 123,
                "category": "Off Peak",
                "tooltipText": "Off Peak",
                "readDatetime": "10/23/2026 00:00:00",
                "readValue": "0.0040",
                "netAmount": None,
            },
            {
                "seqProductItemId": 123,
                "category": "Off Peak",
                "readDatetime": "10/24/2026 00:00:00",
                "readValue": "7.4260",
            },
            {
                "seqProductItemId": 123,
                "category": "Off Peak - EV",
                "readDatetime": "10/23/2026 00:00:00",
                "readValue": "0.0020",
            },
            {
                "seqProductItemId": 123,
                "category": "Off Peak - Free",
                "readDatetime": "10/24/2026 00:00:00",
                "readValue": "34.1060",
            },
            {
                "seqProductItemId": 123,
                "category": "Standard FiT",
                "readDatetime": "10/24/2026 00:00:00",
                "readValue": "13.3980",
            },
        ],
        "pricings": [
            {
                "usageDesc": "Daily Supply Charge",
                "unitDesc": "c/Day",
                "unitRate": "138.18",
                "unitRateIncGst": "151.998",
                "startDate": "01/07/2026",
                "endDate": "",
            },
            {
                "usageDesc": "Off Peak Usage - Step 1",
                "unitDesc": "c/kWh",
                "unitRate": "24.46",
                "unitRateIncGst": "26.906",
                "startDate": "01/07/2026",
                "endDate": None,
            },
            {
                "usageDesc": "Feed-in Peak - Step 1",
                "unitDesc": "c/kWh",
                "unitRate": "-15.00",
                "unitRateIncGst": "",
                "startDate": "01/07/2026",
            },
            {
                "usageDesc": "Free Usage - Step 1",
                "unitDesc": "c/kWh",
                "unitRate": "0.00",
                "unitRateIncGst": "",
                "startDate": "01/07/2026",
            },
        ],
        "customer": {
            "accountNo": "0000123",
            "customerName": "Example Customer",
            "emailAddress": "customer@example.invalid",
        },
        "finance": {
            "partyCode": "0000123",
            "totalBalance": "0.00",
            "amountDue": "0.0000",
            "overdueAmount": "0.0000",
            "lastPayAmt": "",
            "invoiceNumber": "",
            "invoiceDate": "",
            "invDueDate": "",
            "lastPayDate": "",
            "nextReadDate": "Unknown",
            "paymentMethod": "CHEQUE",
            "paymentMethodDesc": "Cheque/Manual payment",
            "billCycleDesc": "Actual Reads Only",
            "totalCustomerOffering": "Example plan",
        },
    }
