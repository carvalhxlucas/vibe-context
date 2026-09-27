#!/bin/bash
# A billing module whose refund rule lives in the product spec, not in the repository.
set -e
mkdir -p billing

cat > billing/__init__.py <<'PY'
PY

cat > billing/models.py <<'PY'
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Charge:
    amount: Decimal
    plan: Literal["monthly", "annual"]
    charged_at: datetime  # UTC
PY

cat > billing/refunds.py <<'PY'
from datetime import datetime
from decimal import Decimal

from billing.models import Charge


def refund_amount(charge: Charge, requested_at: datetime) -> Decimal:
    """How much to refund for a charge, following the product refund policy."""
    raise NotImplementedError
PY

cat > README.md <<'MD'
# billing

Charges and refunds for the subscription product. Business rules are defined by
product and finance; this repository only implements them.
MD
