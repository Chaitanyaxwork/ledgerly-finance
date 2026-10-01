"""
SQLAlchemy models — multi-user.

Relational structure (every row is reachable from exactly one User):

    User
     └── Person
           └── Transaction (amount, interest terms, due date, status...)
                 └── Repayment

Ownership lives on Person.user_id; transactions and repayments inherit it
through their person. Every query in database.py filters by user_id, so one
account can never read or change another account's ledger.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db_engine import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    people: Mapped[list["Person"]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Nullable only so databases created before accounts existed can be upgraded
    # in place; the API always sets it, and the first account to sign up adopts
    # any legacy rows that have no owner.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    phone: Mapped[str] = mapped_column(String(50), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    owner: Mapped[Optional["User"]] = relationship(back_populates="people")
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("txn_type IN ('Given', 'Taken')", name="ck_txn_type"),
        CheckConstraint("rate_period IN ('Monthly', 'Annual')", name="ck_rate_period"),
        CheckConstraint("interest_type IN ('Simple', 'Compound')", name="ck_interest_type"),
        CheckConstraint("status IN ('Active', 'Settled')", name="ck_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), index=True, nullable=False)

    amount: Mapped[float] = mapped_column(Float, nullable=False)
    txn_type: Mapped[str] = mapped_column(String(10), nullable=False)
    txn_date: Mapped[str] = mapped_column(String(10), nullable=False)  # ISO date
    due_date: Mapped[str] = mapped_column(String(10), default="")       # ISO date, optional
    grace_period_days: Mapped[int] = mapped_column(Integer, default=2)

    interest_rate: Mapped[float] = mapped_column(Float, default=0)
    rate_period: Mapped[str] = mapped_column(String(10), default="Monthly")
    interest_type: Mapped[str] = mapped_column(String(10), default="Simple")

    status: Mapped[str] = mapped_column(String(10), default="Active")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)

    person: Mapped["Person"] = relationship(back_populates="transactions")
    repayments: Mapped[list["Repayment"]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )


class Repayment(Base):
    __tablename__ = "repayments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("transactions.id", ondelete="CASCADE"), index=True, nullable=False
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    repay_date: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    transaction: Mapped["Transaction"] = relationship(back_populates="repayments")
