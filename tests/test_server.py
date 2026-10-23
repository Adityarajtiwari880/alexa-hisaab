import pytest
from datetime import date
from server import (
    db, set_up_person, log_visit, log_absences, log_payment, pay_salary,
    month_hisaab, household_hisaab, list_people, undo_last, annual_insights,
    log_pieces, log_penalty, split_bill, list_commitments, add_commitment,
    pay_commitment_amazon_pay, toggle_autopay, total_household_burn, upcoming_reminders
)

@pytest.fixture(autouse=True)
def clean_db(monkeypatch, tmp_path):
    test_db = str(tmp_path / "test_hisaab.db")
    monkeypatch.setenv("HISAAB_DB", test_db)
    monkeypatch.setenv("HISAAB_TODAY", "2026-10-23")
    yield

def test_setup_and_listing():
    res = set_up_person(name="Sunita", pay_amount=9000, pay_unit="month", weekly_off="sunday", paid_leaves_per_month=1, upi_id="sunita@apl")
    assert res["ok"] is True
    assert "Sunita" in res["person"]

    people = list_people()
    assert people["ok"] is True
    assert len(people["people"]) == 1
    assert people["people"][0]["name"] == "Sunita"

def test_absence_and_advances_calculation():
    set_up_person(name="Sunita", pay_amount=9000, pay_unit="month", weekly_off="sunday", paid_leaves_per_month=1, upi_id="sunita@apl")
    
    # Log 3 absences (Oct 5, Oct 12, Oct 19 - all Mondays)
    log_absences(person="Sunita", dates=["2026-10-05", "2026-10-12", "2026-10-19"])
    
    # Log advance of 2000
    pay_res = log_payment(person="Sunita", amount=2000, kind="advance", day="2026-10-23")
    assert pay_res["ok"] is True
    assert pay_res["advance_warning"] is False

    # Check monthly hisaab
    hisaab = month_hisaab(person="Sunita", month="2026-10")
    assert hisaab["ok"] is True
    assert hisaab["owed_now"] == 6419
    assert hisaab["absent_days"] == 3
    assert hisaab["advances"] == 2000
    assert "was absent on 3 of the last 8 Mondays" in hisaab["spoken_summary"]
    assert "upi_url" in hisaab
    assert "qr_url" in hisaab

def test_pay_salary_settlement():
    set_up_person(name="Sunita", pay_amount=9000, pay_unit="month", weekly_off="sunday", paid_leaves_per_month=1, upi_id="sunita@apl")
    log_payment(person="Sunita", amount=2000, kind="advance", day="2026-10-23")
    
    # Settle full salary
    settle_res = pay_salary(person="Sunita", payment_mode="amazon_pay")
    assert settle_res["ok"] is True
    assert settle_res["remaining_balance"] == 0
    assert "Recorded payment" in settle_res["spoken_summary"]

def test_undo_functionality():
    set_up_person(name="Ramesh", pay_amount=500, pay_unit="day")
    log_payment(person="Ramesh", amount=300, kind="advance", day="2026-10-23")
    
    undo_res = undo_last()
    assert undo_res["ok"] is True
    assert undo_res["undone"]["person"] == "Ramesh"

def test_annual_insights_engine():
    set_up_person(name="Sunita", pay_amount=9000, pay_unit="month")
    set_up_person(name="Ramesh", pay_amount=6500, pay_unit="month")
    
    # Sunita takes leave in May 2026 (peak leave month)
    log_absences(person="Sunita", dates=["2026-05-11", "2026-05-12", "2026-05-13", "2026-05-14"])
    
    res = annual_insights(year=2026)
    assert res["ok"] is True
    assert res["year"] == 2026
    assert res["most_leave_person"] == "Sunita"
    assert res["most_leave_peak_month"] == "May"
    assert res["best_attendance_person"] == "Ramesh"
    assert "Sunita took the most leave in May" in res["spoken_summary"]
    assert "Ramesh had the best attendance" in res["spoken_summary"]

def test_dhobi_pieces_counter():
    set_up_person(name="Ramlal Dhobi", pay_amount=5, pay_unit="piece")
    res = log_pieces(person="Ramlal Dhobi", count=12)
    assert res["ok"] is True
    assert res["count"] == 12
    assert res["total_added"] == 60.0
    assert "Logged 12 clothes for Ramlal Dhobi" in res["spoken_summary"]

def test_penalty_deduction():
    set_up_person(name="Ramesh", pay_amount=6500, pay_unit="month")
    res = log_penalty(person="Ramesh", amount=50, reason="broken glass")
    assert res["ok"] is True
    assert res["amount_deducted"] == 50
    assert res["reason"] == "broken glass"
    assert "Deducted 50 rupees from Ramesh for broken glass" in res["spoken_summary"]

def test_flatmate_splitter():
    set_up_person(name="Sunita", pay_amount=4000, pay_unit="month")
    res = split_bill(person="Sunita", split_with="Amit", amount=4000)
    assert res["ok"] is True
    assert res["total_shares"] == 2
    assert res["share_per_person"] == 2000
    assert "Amit" in res["links"]
    assert res["links"]["Amit"]["share"] == 2000
    assert "upi://pay" in res["links"]["Amit"]["upi_url"]
    assert "wa.me" in res["links"]["Amit"]["whatsapp_request"]


def test_commitments_and_autopay():
    add_res = add_commitment(name="iPhone 16 Pro EMI", amount=4200, due_day=5, category="emi", autopay=True)
    assert add_res["ok"] is True
    assert add_res["name"] == "iPhone 16 Pro EMI"
    assert add_res["amount"] == 4200
    assert add_res["autopay_enabled"] is True

    comms = list_commitments()
    assert comms["ok"] is True
    assert comms["total_fixed_amount"] == 4200
    assert comms["autopay_total"] == 4200
    assert len(comms["commitments"]) == 1

    # Toggle AutoPay off
    tog = toggle_autopay(name="iPhone 16 Pro EMI")
    assert tog["ok"] is True
    assert tog["autopay_enabled"] is False

    # Check list again
    comms2 = list_commitments()
    assert comms2["manual_total"] == 4200
    assert comms2["autopay_total"] == 0


def test_pay_commitment_amazon_pay():
    add_commitment(name="Tata Power Electricity", amount=1850, due_day=10, category="utility_bill", autopay=True)
    res = pay_commitment_amazon_pay(name="Tata Power Electricity")
    assert res["ok"] is True
    assert res["amount_paid"] == 1850
    assert "amazonpay@apl" in res["upi_url"]
    assert "amazonpay://pay" in res["amazon_pay_deep_link"]
    assert res["status"] == "paid"
    assert "Processed 1,850 rupees for Tata Power Electricity" in res["spoken_summary"]


def test_total_household_burn():
    # 1. Domestic help
    set_up_person(name="Sunita", pay_amount=9000, pay_unit="month")
    # 2. Fixed commitments
    add_commitment(name="iPhone 16 Pro EMI", amount=4200, due_day=5, category="emi", autopay=True)
    add_commitment(name="Flat Rent", amount=20000, due_day=1, category="rent", autopay=False)

    burn = total_household_burn(month="2026-10")
    assert burn["ok"] is True
    assert burn["domestic_services_total"] == 9000
    assert burn["fixed_commitments_total"] == 24200
    assert burn["total_household_burn"] == 33200
    assert "Your total household burn this month is 33,200 rupees" in burn["spoken_summary"]


def test_upcoming_reminders():
    # Today is 2026-10-23 in test environment
    add_commitment(name="Airtel Wi-Fi", amount=999, due_day=25, category="subscription", autopay=True)
    add_commitment(name="Club Gym", amount=2500, due_day=28, category="subscription", autopay=False)
    add_commitment(name="Next Month Rent", amount=15000, due_day=1, category="rent", autopay=False)

    rem = upcoming_reminders(days_ahead=7)
    assert rem["ok"] is True
    assert rem["upcoming_count"] == 2  # 25th (in 2 days) and 28th (in 5 days)
    assert "Airtel Wi-Fi" in rem["spoken_summary"]
    assert "Club Gym" in rem["spoken_summary"]

