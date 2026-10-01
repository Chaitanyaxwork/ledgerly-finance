"""Pydantic schemas defining the API's request and response contracts."""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------
def _check_password_bytes(value: str) -> str:
    # bcrypt only uses the first 72 bytes; reject longer input instead of silently truncating it.
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password is too long (max 72 bytes).")
    return value


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=72)

    _pw = field_validator("password")(_check_password_bytes)


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=72)


class UserOut(BaseModel):
    id: int
    email: str
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AccountDelete(BaseModel):
    password: str = Field(..., min_length=1, max_length=72)


# ----------------------------------------------------------------------
# People
# ----------------------------------------------------------------------
class PersonCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    phone: Optional[str] = ""
    notes: Optional[str] = ""


class PersonUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    phone: Optional[str] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None


class PersonOut(BaseModel):
    id: int
    name: str
    phone: str
    notes: str
    archived: bool
    created_at: datetime
    updated_at: datetime
    total_given: float
    total_taken: float
    total_repaid: float
    total_interest: float
    outstanding: float
    transaction_count: int
    last_transaction_date: Optional[str] = None


# ----------------------------------------------------------------------
# Transactions
# ----------------------------------------------------------------------
class TransactionCreate(BaseModel):
    person_id: Optional[int] = None
    person_name: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = ""
    amount: float = Field(..., gt=0)
    txn_type: Literal["Given", "Taken"]
    txn_date: date
    due_date: Optional[date] = None
    grace_period_days: int = Field(2, ge=0, le=90)
    interest_rate: float = Field(0, ge=0, le=100)
    rate_period: Literal["Monthly", "Annual"] = "Monthly"
    interest_type: Literal["Simple", "Compound"] = "Simple"
    notes: Optional[str] = ""

    @model_validator(mode="after")
    def _require_person(self):
        if not self.person_id and not (self.person_name and self.person_name.strip()):
            raise ValueError("Provide either person_id (existing person) or person_name (to create one).")
        return self


class StatusUpdate(BaseModel):
    status: Literal["Active", "Settled"]


class DueDateUpdate(BaseModel):
    due_date: Optional[date] = None
    grace_period_days: Optional[int] = Field(None, ge=0, le=90)


class RepaymentCreate(BaseModel):
    transaction_id: int
    amount: float = Field(..., gt=0)
    repay_date: date
    note: Optional[str] = ""


class TransactionOut(BaseModel):
    id: int
    person_id: int
    person_name: str
    phone: str
    txn_type: str
    txn_date: str
    due_date: str
    grace_period_days: int
    due_status: Literal["paid", "upcoming", "due_today", "grace_period", "overdue", "none"]
    days_overdue: int
    days_elapsed: int
    months_elapsed: float
    principal: float
    interest_rate: float
    rate_period: str
    interest_type: str
    accrued_interest: float
    total_due: float
    repaid: float
    balance: float
    status: str
    notes: str


class RepaymentOut(BaseModel):
    id: int
    transaction_id: int
    amount: float
    repay_date: str
    note: str


class OkResponse(BaseModel):
    ok: bool = True


# ----------------------------------------------------------------------
# Standalone interest calculator (no persistence)
# ----------------------------------------------------------------------
class CalculatorRequest(BaseModel):
    amount: float = Field(..., gt=0)
    interest_rate: float = Field(0, ge=0, le=100)
    rate_period: Literal["Monthly", "Annual"] = "Monthly"
    interest_type: Literal["Simple", "Compound"] = "Simple"
    loan_date: date
    due_date: Optional[date] = None
    grace_period_days: int = Field(2, ge=0, le=90)
    payment_date: date


class CalculatorResponse(BaseModel):
    principal: float
    days_elapsed: int
    interest: float
    total_payable: float
    phase: Literal["paid", "upcoming", "due_today", "grace_period", "overdue", "none"]
    days_overdue: int
