"""
Database access layer — SQLAlchemy-based (SQLite or PostgreSQL via DATABASE_URL).

Multi-user rule: every function that reads or changes people, transactions or
repayments takes a `user_id` and filters on it. Ownership lives on
Person.user_id; transactions and repayments are reached through their person,
so a query can never return another account's rows.
"""

from typing import Optional

import pandas as pd
from sqlalchemy import func
from sqlalchemy.orm import Session

from .models import Person, Repayment, Transaction, User


# ----------------------------------------------------------------------
# Users
# ----------------------------------------------------------------------
def create_user(db: Session, email: str, password_hash: str) -> User:
    user = User(email=email, password_hash=password_hash)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def count_users(db: Session) -> int:
    return db.query(func.count(User.id)).scalar() or 0


def adopt_orphaned_people(db: Session, user_id: int) -> int:
    """Give people created before accounts existed (no owner) to this user."""
    n = db.query(Person).filter(Person.user_id.is_(None)).update({Person.user_id: user_id}, synchronize_session=False)
    db.commit()
    return n


def delete_user(db: Session, user_id: int) -> None:
    user = db.get(User, user_id)
    if user:
        db.delete(user)  # cascades: people -> transactions -> repayments
        db.commit()


# ----------------------------------------------------------------------
# People
# ----------------------------------------------------------------------
def create_person(db: Session, user_id: int, name: str, phone: str = "", notes: str = "") -> Person:
    person = Person(user_id=user_id, name=name, phone=phone, notes=notes)
    db.add(person)
    db.commit()
    db.refresh(person)
    return person


def get_person(db: Session, user_id: int, person_id: int) -> Optional[Person]:
    return db.query(Person).filter(Person.id == person_id, Person.user_id == user_id).first()


def find_person_by_name(db: Session, user_id: int, name: str) -> Optional[Person]:
    return (
        db.query(Person)
        .filter(Person.user_id == user_id, func.lower(Person.name) == name.strip().lower())
        .first()
    )


def list_people(db: Session, user_id: int, include_archived: bool = False) -> list[Person]:
    q = db.query(Person).filter(Person.user_id == user_id)
    if not include_archived:
        q = q.filter(Person.archived.is_(False))
    return q.order_by(Person.name.asc()).all()


def update_person(db: Session, user_id: int, person_id: int, **fields) -> Optional[Person]:
    person = get_person(db, user_id, person_id)
    if not person:
        return None
    for key, value in fields.items():
        if value is not None and key in {"name", "phone", "notes", "archived"}:
            setattr(person, key, value)
    db.commit()
    db.refresh(person)
    return person


def delete_person(db: Session, user_id: int, person_id: int) -> None:
    person = get_person(db, user_id, person_id)
    if person:
        db.delete(person)  # cascades to transactions -> repayments
        db.commit()


# ----------------------------------------------------------------------
# Transactions
# ----------------------------------------------------------------------
_TXN_COLUMNS = [
    "id", "person_id", "person_name", "phone", "amount", "txn_type", "txn_date", "due_date",
    "grace_period_days", "interest_rate", "rate_period", "interest_type", "status", "notes",
]


def _owned_transaction(db: Session, user_id: int, transaction_id: int) -> Optional[Transaction]:
    return (
        db.query(Transaction)
        .join(Person, Transaction.person_id == Person.id)
        .filter(Transaction.id == transaction_id, Person.user_id == user_id)
        .first()
    )


def transaction_exists(db: Session, user_id: int, transaction_id: int) -> bool:
    return _owned_transaction(db, user_id, transaction_id) is not None


def add_transaction(db: Session, person_id: int, amount, txn_type, txn_date_val, due_date_val,
                     grace_period_days, interest_rate, rate_period, interest_type, notes) -> int:
    """Caller must already have verified that person_id belongs to the current user."""
    txn = Transaction(
        person_id=person_id, amount=amount, txn_type=txn_type,
        txn_date=txn_date_val.isoformat(), due_date=due_date_val.isoformat() if due_date_val else "",
        grace_period_days=grace_period_days if grace_period_days is not None else 2,
        interest_rate=interest_rate, rate_period=rate_period, interest_type=interest_type,
        status="Active", notes=notes,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn.id


def fetch_transactions(db: Session, user_id: int, transaction_id: Optional[int] = None,
                        person_id: Optional[int] = None, include_archived_people: bool = False) -> pd.DataFrame:
    q = db.query(Transaction).join(Person, Transaction.person_id == Person.id).filter(Person.user_id == user_id)
    if transaction_id is not None:
        q = q.filter(Transaction.id == transaction_id)
    if person_id is not None:
        q = q.filter(Transaction.person_id == person_id)
    if not include_archived_people:
        q = q.filter(Person.archived.is_(False))

    rows = []
    for t in q.order_by(Transaction.txn_date.desc(), Transaction.id.desc()):
        rows.append({
            "id": t.id, "person_id": t.person_id, "person_name": t.person.name, "phone": t.person.phone,
            "amount": t.amount, "txn_type": t.txn_type, "txn_date": t.txn_date, "due_date": t.due_date or "",
            "grace_period_days": t.grace_period_days,
            "interest_rate": t.interest_rate, "rate_period": t.rate_period, "interest_type": t.interest_type,
            "status": t.status, "notes": t.notes,
        })
    return pd.DataFrame(rows, columns=_TXN_COLUMNS)


def update_status(db: Session, user_id: int, transaction_id: int, status: str) -> None:
    txn = _owned_transaction(db, user_id, transaction_id)
    if txn:
        txn.status = status
        db.commit()


def update_transaction_due_date(db: Session, user_id: int, transaction_id: int, due_date_val,
                                 grace_period_days=None) -> None:
    txn = _owned_transaction(db, user_id, transaction_id)
    if txn:
        txn.due_date = due_date_val.isoformat() if due_date_val else ""
        if grace_period_days is not None:
            txn.grace_period_days = grace_period_days
        db.commit()


def delete_transaction(db: Session, user_id: int, transaction_id: int) -> None:
    txn = _owned_transaction(db, user_id, transaction_id)
    if txn:
        db.delete(txn)  # cascades to repayments
        db.commit()


def wipe_all_data(db: Session, user_id: int) -> None:
    """Delete this user's people, transactions and repayments (never anyone else's)."""
    for person in db.query(Person).filter(Person.user_id == user_id).all():
        db.delete(person)  # ORM cascade removes transactions and repayments too
    db.commit()


# ----------------------------------------------------------------------
# Repayments
# ----------------------------------------------------------------------
_REPAY_COLUMNS = ["id", "transaction_id", "amount", "repay_date", "note"]


def fetch_repayments(db: Session, user_id: int, transaction_id: Optional[int] = None) -> pd.DataFrame:
    q = (
        db.query(Repayment)
        .join(Transaction, Repayment.transaction_id == Transaction.id)
        .join(Person, Transaction.person_id == Person.id)
        .filter(Person.user_id == user_id)
    )
    if transaction_id is not None:
        q = q.filter(Repayment.transaction_id == transaction_id)
    rows = [{col: getattr(r, col) for col in _REPAY_COLUMNS} for r in q.order_by(Repayment.repay_date.desc())]
    return pd.DataFrame(rows, columns=_REPAY_COLUMNS)


def add_repayment(db: Session, transaction_id: int, amount: float, repay_date_val, note: str) -> int:
    """Caller must already have verified the transaction belongs to the current user."""
    repay = Repayment(transaction_id=transaction_id, amount=amount, repay_date=repay_date_val.isoformat(), note=note)
    db.add(repay)
    db.commit()
    db.refresh(repay)
    return repay.id
