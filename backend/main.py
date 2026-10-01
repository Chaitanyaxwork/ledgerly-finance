"""
Ledgerly / Credit Ledger Tracker — FastAPI backend (multi-user, login required).

Run locally (from the repository root):

    python run.py
    # or: uvicorn backend.main:app --reload --port 8000

Interactive API docs: http://localhost:8000/docs
"""

from collections import defaultdict
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import auth, calculations as calc, config, database as db, models  # noqa: F401  (registers tables)
from .db_engine import Base, engine, get_db
from .models import User
from .schema_sync import sync_schema
from .schemas import (
    AccountDelete,
    CalculatorRequest,
    CalculatorResponse,
    DueDateUpdate,
    OkResponse,
    PersonCreate,
    PersonOut,
    PersonUpdate,
    RepaymentCreate,
    RepaymentOut,
    StatusUpdate,
    TransactionCreate,
    TransactionOut,
    Token,
    UserCreate,
    UserLogin,
    UserOut,
)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR.parent / "frontend"

app = FastAPI(
    title="Ledgerly API",
    description="REST API for tracking personal money given and taken, with automatic interest, due dates, and grace periods.",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Creates missing tables and adds any missing columns (e.g. an older
# ledgerly.db that predates `grace_period_days`), so an existing database
# never breaks the API. Safe to run on every start.
sync_schema(engine, Base.metadata)


@app.middleware("http")
async def no_cache_frontend(request, call_next):
    """Stop browsers from serving a stale copy of the old frontend."""
    response = await call_next(request)
    if not request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/api/health")
def health_check():
    return {"status": "ok"}


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
def _full_ledger(session: Session, user_id: int, person_id: int | None = None) -> list[dict]:
    df_txn = db.fetch_transactions(session, user_id, person_id=person_id)
    df_repay = db.fetch_repayments(session, user_id)
    return calc.build_ledger(df_txn, df_repay, date.today())


def _person_out(person, ledger_rows: list[dict]) -> PersonOut:
    mine = [r for r in ledger_rows if r["person_id"] == person.id]
    given = [r for r in mine if r["txn_type"] == "Given"]
    taken = [r for r in mine if r["txn_type"] == "Taken"]
    total_given = round(sum(r["principal"] for r in given), 2)
    total_taken = round(sum(r["principal"] for r in taken), 2)
    total_repaid = round(sum(r["repaid"] for r in mine), 2)
    total_interest = round(sum(r["accrued_interest"] for r in mine), 2)
    outstanding = round(sum(r["balance"] for r in given) - sum(r["balance"] for r in taken), 2)
    last_date = max((r["txn_date"] for r in mine), default=None)
    return PersonOut(
        id=person.id, name=person.name, phone=person.phone, notes=person.notes,
        archived=person.archived, created_at=person.created_at, updated_at=person.updated_at,
        total_given=total_given, total_taken=total_taken, total_repaid=total_repaid,
        total_interest=total_interest, outstanding=outstanding,
        transaction_count=len(mine), last_transaction_date=last_date,
    )


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------
def _token_response(user: User) -> Token:
    return Token(access_token=auth.create_access_token(user.id), user=UserOut.model_validate(user, from_attributes=True))


@app.post("/api/auth/signup", response_model=Token, status_code=201)
def signup(payload: UserCreate, session: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.get_user_by_email(session, email):
        raise HTTPException(status_code=409, detail="An account with this email already exists. Try signing in.")
    first_user = db.count_users(session) == 0
    user = db.create_user(session, email, auth.hash_password(payload.password))
    if first_user:
        # Data from before accounts existed belongs to whoever set the app up.
        db.adopt_orphaned_people(session, user.id)
    return _token_response(user)


@app.post("/api/auth/login", response_model=Token)
def login(payload: UserLogin, request: Request, session: Session = Depends(get_db)):
    email = payload.email.lower()
    throttle_key = f"{request.client.host if request.client else 'unknown'}|{email}"
    auth.check_login_allowed(throttle_key)
    user = db.get_user_by_email(session, email)
    if not auth.verify_password(payload.password, user.password_hash if user else None):
        auth.record_login_failure(throttle_key)
        raise HTTPException(status_code=401, detail="Incorrect email or password.")
    auth.clear_login_failures(throttle_key)
    return _token_response(user)


@app.get("/api/auth/me", response_model=UserOut)
def me(user: User = Depends(auth.get_current_user)):
    return user


@app.delete("/api/auth/me", response_model=OkResponse)
def delete_account(payload: AccountDelete, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    """Permanently deletes the account and every person, transaction and repayment it owns."""
    if not auth.verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=403, detail="Password is incorrect.")
    db.delete_user(session, user.id)
    return OkResponse()


# ----------------------------------------------------------------------
# People
# ----------------------------------------------------------------------
@app.get("/api/people", response_model=list[PersonOut])
def list_people(include_archived: bool = False, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    people = db.list_people(session, user.id, include_archived=include_archived)
    ledger = _full_ledger(session, user.id)
    return [_person_out(p, ledger) for p in people]


@app.post("/api/people", response_model=PersonOut, status_code=201)
def create_person(payload: PersonCreate, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    person = db.create_person(session, user.id, payload.name.strip(), (payload.phone or "").strip(), (payload.notes or "").strip())
    return _person_out(person, [])


@app.get("/api/people/{person_id}", response_model=PersonOut)
def get_person(person_id: int, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    person = db.get_person(session, user.id, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return _person_out(person, _full_ledger(session, user.id, person_id=person_id))


@app.patch("/api/people/{person_id}", response_model=PersonOut)
def update_person(person_id: int, payload: PersonUpdate, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    updates = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    if "name" in updates and updates["name"]:
        updates["name"] = updates["name"].strip()
    person = db.update_person(session, user.id, person_id, **updates)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return _person_out(person, _full_ledger(session, user.id, person_id=person_id))


@app.delete("/api/people/{person_id}", response_model=OkResponse)
def delete_person(person_id: int, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if not db.get_person(session, user.id, person_id):
        raise HTTPException(status_code=404, detail="Person not found")
    db.delete_person(session, user.id, person_id)
    return OkResponse()


# ----------------------------------------------------------------------
# Transactions
# ----------------------------------------------------------------------
@app.get("/api/transactions", response_model=list[TransactionOut])
def list_transactions(person_id: int | None = None, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    return _full_ledger(session, user.id, person_id=person_id)


@app.post("/api/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(payload: TransactionCreate, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if payload.person_id:
        person = db.get_person(session, user.id, payload.person_id)  # must belong to this user
        if not person:
            raise HTTPException(status_code=404, detail="Person not found")
    else:
        person = db.find_person_by_name(session, user.id, payload.person_name)
        if not person:
            person = db.create_person(session, user.id, payload.person_name.strip(), (payload.phone or "").strip())

    new_id = db.add_transaction(
        session, person.id, payload.amount, payload.txn_type, payload.txn_date, payload.due_date,
        payload.grace_period_days, payload.interest_rate, payload.rate_period, payload.interest_type,
        (payload.notes or "").strip(),
    )
    return calc.build_ledger(
        db.fetch_transactions(session, user.id, transaction_id=new_id), db.fetch_repayments(session, user.id, new_id), date.today()
    )[0]


@app.patch("/api/transactions/{transaction_id}", response_model=OkResponse)
def set_transaction_status(transaction_id: int, payload: StatusUpdate, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if not db.transaction_exists(session, user.id, transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.update_status(session, user.id, transaction_id, payload.status)
    return OkResponse()


@app.patch("/api/transactions/{transaction_id}/due-date", response_model=OkResponse)
def set_transaction_due_date(transaction_id: int, payload: DueDateUpdate, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if not db.transaction_exists(session, user.id, transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.update_transaction_due_date(session, user.id, transaction_id, payload.due_date, payload.grace_period_days)
    return OkResponse()


@app.delete("/api/transactions/{transaction_id}", response_model=OkResponse)
def remove_transaction(transaction_id: int, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if not db.transaction_exists(session, user.id, transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete_transaction(session, user.id, transaction_id)
    return OkResponse()


# ----------------------------------------------------------------------
# Repayments (auto-settles the transaction when the balance reaches zero)
# ----------------------------------------------------------------------
@app.post("/api/repayments", response_model=RepaymentOut, status_code=201)
def create_repayment(payload: RepaymentCreate, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if not db.transaction_exists(session, user.id, payload.transaction_id):
        raise HTTPException(status_code=404, detail="Transaction not found")

    new_id = db.add_repayment(session, payload.transaction_id, payload.amount, payload.repay_date, (payload.note or "").strip())

    updated = calc.build_ledger(
        db.fetch_transactions(session, user.id, transaction_id=payload.transaction_id),
        db.fetch_repayments(session, user.id, payload.transaction_id),
        date.today(),
    )[0]
    if updated["balance"] <= 0 and updated["status"] == "Active":
        db.update_status(session, user.id, payload.transaction_id, "Settled")

    return RepaymentOut(
        id=new_id, transaction_id=payload.transaction_id, amount=payload.amount,
        repay_date=payload.repay_date.isoformat(), note=(payload.note or "").strip(),
    )


@app.get("/api/repayments", response_model=list[RepaymentOut])
def list_repayments(transaction_id: int | None = None, session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    df = db.fetch_repayments(session, user.id, transaction_id)
    return [
        RepaymentOut(
            id=int(r["id"]), transaction_id=int(r["transaction_id"]),
            amount=round(float(r["amount"]), 2), repay_date=str(r["repay_date"]), note=str(r["note"] or ""),
        )
        for r in df.to_dict("records")
    ]


# ----------------------------------------------------------------------
# Interest calculator (standalone what-if tool, no persistence)
# ----------------------------------------------------------------------
@app.post("/api/calculator", response_model=CalculatorResponse)
def run_calculator(payload: CalculatorRequest, user: User = Depends(auth.get_current_user)):
    return calc.calculate_scenario(
        payload.amount, payload.interest_rate, payload.rate_period, payload.interest_type,
        payload.loan_date, payload.due_date, payload.grace_period_days, payload.payment_date,
    )


# ----------------------------------------------------------------------
# Reports — monthly summary, computed from the same ledger everything else uses
# ----------------------------------------------------------------------
@app.get("/api/reports/monthly")
def monthly_report(session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    ledger = _full_ledger(session, user.id)
    df_repay = db.fetch_repayments(session, user.id)

    months: dict[str, dict] = defaultdict(lambda: {
        "month": "", "money_given": 0.0, "money_taken": 0.0, "repayments": 0.0,
        "interest_earned": 0.0, "interest_paid": 0.0,
    })
    for row in ledger:
        month_key = row["txn_date"][:7]  # "YYYY-MM"
        bucket = months[month_key]
        bucket["month"] = month_key
        if row["txn_type"] == "Given":
            bucket["money_given"] += row["principal"]
            bucket["interest_earned"] += row["accrued_interest"]
        else:
            bucket["money_taken"] += row["principal"]
            bucket["interest_paid"] += row["accrued_interest"]

    for _, r in df_repay.iterrows():
        month_key = str(r["repay_date"])[:7]
        months[month_key]["month"] = month_key
        months[month_key]["repayments"] += float(r["amount"])

    outstanding = round(
        sum(r["balance"] for r in ledger if r["txn_type"] == "Given")
        - sum(r["balance"] for r in ledger if r["txn_type"] == "Taken"),
        2,
    )

    result = [
        {**v, **{k: round(v[k], 2) for k in ("money_given", "money_taken", "repayments", "interest_earned", "interest_paid")}}
        for v in months.values()
    ]
    result.sort(key=lambda r: r["month"])
    return {"months": result, "outstanding": outstanding}


# ----------------------------------------------------------------------
# Danger zone
# ----------------------------------------------------------------------
@app.delete("/api/reset", response_model=OkResponse)
def reset_all_data(session: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    db.wipe_all_data(session, user.id)
    return OkResponse()


# ----------------------------------------------------------------------
# Static frontend (mounted last so it doesn't shadow the /api/* routes)
# ----------------------------------------------------------------------
if (FRONTEND_DIR / "index.html").exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    from fastapi.responses import PlainTextResponse

    @app.get("/", response_class=PlainTextResponse)
    def missing_frontend():
        return f"Frontend not found. Expected {FRONTEND_DIR / 'index.html'} — keep the 'frontend' folder next to 'backend'."
