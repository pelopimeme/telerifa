"""Pure-logic tests (no database).   pytest tests/"""
import importlib.util
from decimal import Decimal
from pathlib import Path

_spec = importlib.util.spec_from_file_location("money", Path(__file__).parent.parent / "app/services/money.py")
money = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(money)
D = Decimal


def test_total_ves_formula():
    assert money.calc_total_ves(3, D("1.00"), D("36.5000")) == D("109.50")
    assert money.calc_total_ves(1, D("1.00"), D("36.4549")) == D("36.45")
    assert money.calc_total_ves(1, D("1.00"), D("36.4550")) == D("36.46")          # half-up
    assert money.calc_total_ves(10, D("1.00"), D("40.1234")) == D("401.23")


def test_fmt_ves_uses_venezuelan_separators():
    assert money.fmt_ves(D("3912.34")) == "3.912,34"
    assert money.fmt_ves("109.5") == "109,50"
    assert money.fmt_ves(0) == "0,00"


def test_extract_rate_variants():
    assert money.extract_rate({"current": {"usd": 36.5, "eur": 40.1}}) == D("36.5000")
    assert money.extract_rate({"usd": {"promedio": "36,5012", "fecha": "2026-10-20"}}) == D("36.5012")
    assert money.extract_rate({"monitors": {"usd": {"price": 36.5, "last_update": "2026"}}}) == D("36.5000")
    assert money.extract_rate({"data": [{"currency": "x", "dolar": "1.234,56"}]}) == D("1234.5600")


def test_extract_rate_rejects_non_usd():
    assert money.extract_rate({"eur": 40.1}) is None
    assert money.extract_rate({"previous": {"usd": 35}}) is None
    assert money.extract_rate({"hello": "world"}) is None
