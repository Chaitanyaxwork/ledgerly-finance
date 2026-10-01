"""
Interest calculation and ledger-building logic for the Ledgerly / Credit
Ledger Tracker. Ported from the original Streamlit prototype's pandas
logic, unchanged in method, so figures match exactly.

The API layer is the only thing that changed: this module now returns
plain, JSON-friendly Python types instead of formatted display strings,
since formatting (currency symbols, date display, etc.) is the
frontend's job in a decoupled architecture.
"""

from datetime import date, datetime, timedelta

import pandas as pd

DAYS_PER_MONTH = 30.4368   # average Gregorian month length
DAYS_PER_YEAR = 365.25
DEFAULT_GRACE_PERIOD_DAYS = 2


def _parse_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def due_status(status: str, due_date_str: str, grace_period_days: int, as_of: date) -> str:
    """One of: 'paid', 'upcoming', 'due_today', 'grace_period', 'overdue', 'none' (no due date set)."""
    if status == "Settled":
        return "paid"
    due = _parse_date(due_date_str)
    if due is None:
        return "none"
    grace_end = due + timedelta(days=max(int(grace_period_days or 0), 0))
    if as_of < due:
        return "upcoming"
    if as_of == due:
        return "due_today"
    if as_of <= grace_end:
        return "grace_period"
    return "overdue"


def days_overdue(due_date_str: str, grace_period_days: int, as_of: date) -> int:
    """Days past the END of the grace period. 0 if not overdue / no due date."""
    due = _parse_date(due_date_str)
    if due is None:
        return 0
    grace_end = due + timedelta(days=max(int(grace_period_days or 0), 0))
    return max((as_of - grace_end).days, 0)


def elapsed_time(start: date, as_of: date):
    """Days, and fractional months / years, between two dates."""
    days = max((as_of - start).days, 0)
    months = days / DAYS_PER_MONTH
    years = days / DAYS_PER_YEAR
    return days, months, years


def compute_interest(principal: float, rate_pct: float, rate_period: str,
                      interest_type: str, months: float, years: float) -> float:
    """
    Interest accrued so far.
    - `rate_pct` is interpreted in whichever period the user chose, e.g.
      "2% Monthly" uses fractional months elapsed; "10% Annual" uses
      fractional years elapsed.
    - Compound interest uses fractional compounding, P * ((1+r)**t - 1),
      so interest accrues smoothly day by day instead of jumping only on
      whole-period boundaries.
    """
    if rate_pct <= 0 or principal <= 0:
        return 0.0
    r = rate_pct / 100.0
    t = months if rate_period == "Monthly" else years
    if t <= 0:
        return 0.0
    if interest_type == "Compound":
        interest = principal * ((1 + r) ** t - 1)
    else:
        interest = principal * r * t
    return round(interest, 2)


def build_ledger(df_txn: pd.DataFrame, df_repay: pd.DataFrame, as_of: date) -> list[dict]:
    """
    Turn raw transaction + repayment rows into a full breakdown per
    transaction: Principal + Accrued Interest = Total Due, less Repaid
    = Balance. Returns a list of plain dicts with native Python types,
    ready to serialize as JSON / validate against a Pydantic model.
    """
    if df_txn.empty:
        return []

    rows = []
    for _, txn in df_txn.iterrows():
        start = datetime.fromisoformat(txn["txn_date"]).date()
        days, months, years = elapsed_time(start, as_of)
        interest = compute_interest(
            float(txn["amount"]), float(txn["interest_rate"]), txn["rate_period"],
            txn["interest_type"], months, years,
        )
        principal = round(float(txn["amount"]), 2)
        total_due = round(principal + interest, 2)
        repaid = 0.0
        if not df_repay.empty:
            repaid = float(df_repay.loc[df_repay["transaction_id"] == txn["id"], "amount"].sum())
        repaid = round(repaid, 2)
        balance = round(total_due - repaid, 2)

        gp_days = int(txn.get("grace_period_days") if txn.get("grace_period_days") not in (None, "") else DEFAULT_GRACE_PERIOD_DAYS)
        due_str = str(txn.get("due_date") or "")

        rows.append({
            "id": int(txn["id"]),
            "person_id": int(txn["person_id"]),
            "person_name": str(txn["person_name"]),
            "phone": str(txn["phone"] or ""),
            "txn_type": str(txn["txn_type"]),
            "txn_date": start.isoformat(),
            "due_date": due_str,
            "grace_period_days": gp_days,
            "due_status": due_status(str(txn["status"]), due_str, gp_days, as_of),
            "days_overdue": days_overdue(due_str, gp_days, as_of),
            "days_elapsed": int(days),
            "months_elapsed": round(float(months), 2),
            "principal": principal,
            "interest_rate": round(float(txn["interest_rate"]), 4),
            "rate_period": str(txn["rate_period"]),
            "interest_type": str(txn["interest_type"]),
            "accrued_interest": interest,
            "total_due": total_due,
            "repaid": repaid,
            "balance": balance,
            "status": str(txn["status"]),
            "notes": str(txn["notes"] or ""),
        })
    return rows


def calculate_scenario(principal: float, rate_pct: float, rate_period: str, interest_type: str,
                        loan_date: date, due_date: date | None, grace_period_days: int,
                        payment_date: date) -> dict:
    """
    Standalone what-if calculator (no persistence): given a hypothetical loan,
    its due date and grace period, and a hypothetical payment date, compute
    the interest accrued by that payment date and which phase it falls in.
    """
    days, months, years = elapsed_time(loan_date, payment_date)
    interest = compute_interest(principal, rate_pct, rate_period, interest_type, months, years)
    total_payable = round(principal + interest, 2)
    due_str = due_date.isoformat() if due_date else ""
    phase = due_status("Active", due_str, grace_period_days, payment_date)
    overdue = days_overdue(due_str, grace_period_days, payment_date)
    return {
        "principal": round(principal, 2),
        "days_elapsed": days,
        "interest": interest,
        "total_payable": total_payable,
        "phase": phase,
        "days_overdue": overdue,
    }
