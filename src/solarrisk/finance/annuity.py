"""Annuity (level-payment) loan mathematics.

A loan of principal D at periodic rate r over n periods with a constant payment A satisfies
    D = Σ_{k=1..n} A / (1+r)^k = A · [1 − (1+r)^−n] / r
so
    A = D · r / [1 − (1+r)^−n]                        (annuity formula)
Each period: interest_k = r · balance_{k−1};  principal_k = A − interest_k;  balance_k = balance_{k−1} − principal_k.
Interest falls and principal rises over time; the sum of principal repayments equals D exactly.
"""

from __future__ import annotations

import pandas as pd


def annuity_payment(principal: float, rate: float, n: int) -> float:
    if n <= 0:
        raise ValueError("n must be positive")
    if principal <= 0:
        return 0.0
    if rate == 0:
        return principal / n
    return principal * rate / (1 - (1 + rate) ** (-n))


def amortization_schedule(principal: float, rate: float, n: int) -> pd.DataFrame:
    a = annuity_payment(principal, rate, n)
    rows, bal = [], principal
    for k in range(1, n + 1):
        interest = bal * rate
        princ = a - interest
        rows.append({"year": k, "opening_balance": bal, "interest": interest, "principal": princ,
                     "debt_service": a, "closing_balance": bal - princ})
        bal -= princ
    return pd.DataFrame(rows).set_index("year")
