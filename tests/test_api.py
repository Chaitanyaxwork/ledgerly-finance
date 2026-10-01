"""
Integration tests for the Ledgerly / Credit Ledger Tracker API.

Run from the repository root:

    pip install -r backend/requirements.txt
    pip install httpx   # only needed to run these tests, not the app itself
    python3 tests/test_api.py

Uses FastAPI's TestClient against a real (temporary) SQLite database.
"""

import os
import sys
import tempfile

# Use a throwaway database so the tests (which call /api/reset) never touch real data.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "test.db").replace("\\", "/")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta

from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)

AUTH = {}  # filled in by the auth section below, used as the default for every call


def get(path, **kw):
    kw.setdefault("headers", AUTH)
    return client.get(path, **kw)


def post(path, **kw):
    kw.setdefault("headers", AUTH)
    return client.post(path, **kw)


def patch(path, **kw):
    kw.setdefault("headers", AUTH)
    return client.patch(path, **kw)


def delete(path, **kw):
    kw.setdefault("headers", AUTH)
    return client.delete(path, **kw)


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        raise SystemExit(f"Test failed: {label}")


def run():
    print("=== Health check (public, no token needed) ===")
    check("health returns 200", client.get("/api/health").status_code == 200)

    print("\n=== Protected routes reject requests with no token ===")
    check("no token -> 401", client.get("/api/people").status_code == 401)
    check("bad token -> 401", client.get("/api/people", headers={"Authorization": "Bearer garbage"}).status_code == 401)

    print("\n=== Sign up ===")
    r = client.post("/api/auth/signup", json={"email": "Owner@Example.com", "password": "correct horse battery"})
    check("signup 201", r.status_code == 201)
    body = r.json()
    check("signup returns a token", bool(body.get("access_token")))
    check("signup returns the user, lowercased email", body["user"]["email"] == "owner@example.com")
    AUTH["Authorization"] = f"Bearer {body['access_token']}"

    print("\n=== Duplicate signup is rejected ===")
    check("duplicate email -> 409", client.post("/api/auth/signup", json={"email": "owner@example.com", "password": "whatever123"}).status_code == 409)

    print("\n=== Login ===")
    r = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "correct horse battery"})
    check("login 200", r.status_code == 200)
    check("wrong password -> 401", client.post("/api/auth/login", json={"email": "owner@example.com", "password": "nope"}).status_code == 401)
    check("unknown email -> 401 (not 404 - don't reveal which emails exist)", client.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever123"}).status_code == 401)

    print("\n=== /api/auth/me reflects the signed-in user ===")
    check("me returns the right email", get("/api/auth/me").json()["email"] == "owner@example.com")

    print("\n=== Create a person explicitly ===")
    r = post("/api/people", json={"name": "Ravi Kumar", "phone": "+91 98765 43210", "notes": "Neighbour"})
    check("create person 201", r.status_code == 201)
    person = r.json()
    check("aggregates start at zero", person["outstanding"] == 0 and person["transaction_count"] == 0)
    person_id = person["id"]

    print("\n=== Create a Given transaction against that person, with a due date ===")
    txn_date = (date.today() - timedelta(days=100)).isoformat()
    due_date = (date.today() + timedelta(days=3)).isoformat()  # still "upcoming"
    payload = {
        "person_id": person_id, "amount": 10000, "txn_type": "Given", "txn_date": txn_date,
        "due_date": due_date, "interest_rate": 2, "rate_period": "Monthly", "interest_type": "Simple", "notes": "test",
    }
    r = post("/api/transactions", json=payload)
    check("create txn 201", r.status_code == 201)
    txn = r.json()
    expected_interest = round(10000 * 0.02 * (100 / 30.4368), 2)
    check("interest matches manual calc", txn["accrued_interest"] == expected_interest)
    check("due_status is upcoming", txn["due_status"] == "upcoming")
    check("default grace period is 2 days", txn["grace_period_days"] == 2)
    txn_id = txn["id"]

    print("\n=== Due-date phase model: due today, grace period, overdue ===")
    r = post("/api/transactions", json={
        "person_id": person_id, "amount": 100, "txn_type": "Given",
        "txn_date": date.today().isoformat(), "due_date": date.today().isoformat(), "interest_rate": 0,
    })
    check("due_today phase", r.json()["due_status"] == "due_today")

    r = post("/api/transactions", json={
        "person_id": person_id, "amount": 100, "txn_type": "Given",
        "txn_date": date.today().isoformat(), "due_date": (date.today() - timedelta(days=1)).isoformat(),
        "grace_period_days": 2, "interest_rate": 0,
    })
    check("grace_period phase (1 day past due, 2-day grace)", r.json()["due_status"] == "grace_period")
    check("not yet counted as overdue days", r.json()["days_overdue"] == 0)

    r = post("/api/transactions", json={
        "person_id": person_id, "amount": 100, "txn_type": "Given",
        "txn_date": date.today().isoformat(), "due_date": (date.today() - timedelta(days=5)).isoformat(),
        "grace_period_days": 2, "interest_rate": 0,
    })
    check("overdue phase (past grace)", r.json()["due_status"] == "overdue")
    check("days_overdue counts from END of grace period", r.json()["days_overdue"] == 3)  # due -5d, grace ends -3d, so 3 days overdue

    print("\n=== Create a second transaction by person_name only (auto find-or-create) ===")
    r = post("/api/transactions", json={
        "person_name": "Ravi Kumar", "amount": 500, "txn_type": "Given",
        "txn_date": date.today().isoformat(), "interest_rate": 0,
    })
    check("second txn also 201", r.status_code == 201)
    check("reused the same person, did not duplicate", r.json()["person_id"] == person_id)
    r = get("/api/people")
    check("still exactly one person", len(r.json()) == 1)
    check("person now has 5 transactions", r.json()[0]["transaction_count"] == 5)

    print("\n=== Overdue detection ===")
    r = post("/api/transactions", json={
        "person_id": person_id, "amount": 1000, "txn_type": "Given",
        "txn_date": date.today().isoformat(), "due_date": (date.today() - timedelta(days=5)).isoformat(),
        "interest_rate": 0,
    })
    check("overdue txn created", r.status_code == 201)
    check("due_status is overdue", r.json()["due_status"] == "overdue")

    print("\n=== Reject invalid payload (no person given) ===")
    r = post("/api/transactions", json={"amount": 100, "txn_type": "Given", "txn_date": date.today().isoformat()})
    check("422 without person_id or person_name", r.status_code == 422)

    print("\n=== Repayment auto-settles when balance reaches zero ===")
    r = post("/api/repayments", json={
        "transaction_id": txn_id, "amount": round(10000 + expected_interest, 2), "repay_date": date.today().isoformat(),
    })
    check("repayment 201", r.status_code == 201)
    updated = [t for t in get("/api/transactions").json() if t["id"] == txn_id][0]
    check("balance is zero", updated["balance"] == 0)
    check("status auto-changed to Settled", updated["status"] == "Settled")

    print("\n=== Partial repayment does NOT auto-settle ===")
    r = get("/api/transactions").json()
    active_1000 = [t for t in r if t["principal"] == 1000][0]
    post("/api/repayments", json={
        "transaction_id": active_1000["id"], "amount": 200, "repay_date": date.today().isoformat(),
    })
    still = [t for t in get("/api/transactions").json() if t["id"] == active_1000["id"]][0]
    check("still Active after partial repayment", still["status"] == "Active")
    check("balance reduced by 200", still["balance"] == active_1000["balance"] - 200)

    print("\n=== Monthly report responds ===")
    r = get("/api/reports/monthly")
    check("report 200", r.status_code == 200)
    check("report has months + outstanding", "months" in r.json() and "outstanding" in r.json())

    print("\n=== Archive a person (soft delete) ===")
    r = patch(f"/api/people/{person_id}", json={"archived": True})
    check("archive 200", r.status_code == 200)
    check("archived flag set", r.json()["archived"] is True)
    check("archived person hidden from default list", get("/api/people").json() == [])
    check("archived person still visible with include_archived", len(get("/api/people?include_archived=true").json()) == 1)
    check("archived person's transactions hidden from ledger", get("/api/transactions").json() == [])

    print("\n=== Standalone interest calculator ===")
    r = post("/api/calculator", json={
        "amount": 10000, "interest_rate": 2, "rate_period": "Monthly", "interest_type": "Simple",
        "loan_date": (date.today() - timedelta(days=100)).isoformat(),
        "due_date": (date.today() - timedelta(days=5)).isoformat(),
        "grace_period_days": 2,
        "payment_date": date.today().isoformat(),
    })
    check("calculator 200", r.status_code == 200)
    calc = r.json()
    check("calculator interest matches manual calc", calc["interest"] == expected_interest)
    check("calculator total_payable = principal + interest", calc["total_payable"] == round(10000 + expected_interest, 2))
    check("calculator phase is overdue", calc["phase"] == "overdue")
    check("calculator days_overdue is 3", calc["days_overdue"] == 3)

    print("\n=== Data isolation between two accounts ===")
    r = client.post("/api/auth/signup", json={"email": "other@example.com", "password": "a totally different pw"})
    check("second signup 201", r.status_code == 201)
    other_auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
    check("second account starts with zero people (does not see the first account's data)",
          client.get("/api/people", headers=other_auth).json() == [])
    check("second account cannot fetch the first account's person by ID",
          client.get(f"/api/people/{person_id}", headers=other_auth).status_code == 404)
    check("second account cannot fetch the first account's transaction by ID",
          client.get("/api/transactions", headers=other_auth).json() == [])
    r = client.patch(f"/api/transactions/{txn_id}", json={"status": "Settled"}, headers=other_auth)
    check("second account cannot modify the first account's transaction", r.status_code == 404)
    r = client.post("/api/repayments", json={"transaction_id": txn_id, "amount": 1, "repay_date": date.today().isoformat()}, headers=other_auth)
    check("second account cannot log a repayment against the first account's transaction", r.status_code == 404)
    r = client.delete("/api/reset", headers=other_auth)
    check("second account's reset only wipes its own (empty) data", r.status_code == 200)
    check("first account's person still exists after the second account's reset",
          get(f"/api/people/{person_id}").status_code == 200)

    print("\n=== Login throttling after repeated failures ===")
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong"})
    r = client.post("/api/auth/login", json={"email": "owner@example.com", "password": "wrong"})
    check("6th failed attempt is throttled (429)", r.status_code == 429)
    check("correct password is also throttled during cooldown (protects the real owner too)",
          client.post("/api/auth/login", json={"email": "owner@example.com", "password": "correct horse battery"}).status_code == 429)

    print("\n=== Reset wipes everything ===")
    r = delete("/api/reset")
    check("reset 200", r.status_code == 200)
    check("people gone too", get("/api/people?include_archived=true").json() == [])

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    run()
