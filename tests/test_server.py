import pytest
from datetime import date
from server import db, set_up_person, log_visit, log_absences, log_payment, pay_salary, month_hisaab, household_hisaab, list_people, undo_last

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
