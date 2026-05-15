"""Calculator engine tests — pure functions, no DB."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.calculator import (
    CalcInput,
    Catalog,
    FinishConfig,
    ProductYield,
    calculate,
    strip_prices,
)


def _yield(**kwargs) -> ProductYield:
    base = {
        "id": 1,
        "product_name": "Base Material",
        "sku_size": "1 GL",
        "price_retail": Decimal("50.00"),
        "price_wholesale": None,
        "coverage_per_unit": Decimal("100"),
        "coverage_unit": "sqft",
        "finish_group": "matte",
        "product_category": "base",
        "pack_size": "small",
    }
    base.update(kwargs)
    return ProductYield(**base)


def _config(**kwargs) -> FinishConfig:
    base = {
        "finish_type": "matte",
        "finish_group": "matte",
        "display_name": "Matte",
        "required_addons": [],
        "sort_order": 1,
    }
    base.update(kwargs)
    return FinishConfig(**base)


def _catalog(yields, configs) -> Catalog:
    yields_by_group: dict[str, list[ProductYield]] = {}
    for y in yields:
        yields_by_group.setdefault(y.finish_group, []).append(y)
    return Catalog(
        yields_by_group=yields_by_group,
        configs_by_type={c.finish_type: c for c in configs},
    )


def test_calculate_single_finish_single_product():
    catalog = _catalog([_yield()], [_config()])
    result = calculate(CalcInput(finish_type="matte", target_coverage=250.0), catalog)
    assert len(result.sections) == 1
    section = result.sections[0]
    assert section.finish == "Matte"
    assert len(section.items) == 1
    item = section.items[0]
    # 250 sqft / 100 per gallon → 3 units (ceil)
    assert item.units_required == 3
    assert item.line_total == Decimal("150.00")
    assert result.subtotal == Decimal("150.00")


def test_calculate_prefers_large_pack_when_available():
    """Large pack should be chosen when present — usually more efficient per sqft."""
    small = _yield(sku_size="1 GL", price_retail=Decimal("50"), coverage_per_unit=Decimal("100"), pack_size="small")
    large = _yield(sku_size="5 GL", price_retail=Decimal("200"), coverage_per_unit=Decimal("500"), pack_size="large")
    catalog = _catalog([small, large], [_config()])
    result = calculate(CalcInput(finish_type="matte", target_coverage=600.0), catalog)
    item = result.sections[0].items[0]
    # 600 sqft / 500 → 2 large units * $200 = $400
    assert item.sku_size == "5 GL"
    assert item.units_required == 2
    assert item.line_total == Decimal("400")


def test_calculate_with_required_addons():
    base = _yield(product_category="base")
    primer = _yield(product_category="primer", product_name="Primer", price_retail=Decimal("30"))
    catalog = _catalog([base, primer], [_config(required_addons=["primer"])])
    result = calculate(CalcInput(finish_type="matte", target_coverage=100.0), catalog)
    section = result.sections[0]
    assert len(section.items) == 1
    assert len(section.addons) == 1
    assert section.addons[0].product_name == "Primer"


def test_calculate_unknown_finish_raises_value_error():
    catalog = _catalog([_yield()], [_config()])
    with pytest.raises(ValueError, match="unknown finish_type"):
        calculate(CalcInput(finish_type="glossy", target_coverage=100), catalog)


def test_calculate_negative_target_raises_value_error():
    catalog = _catalog([_yield()], [_config()])
    with pytest.raises(ValueError, match="target_coverage"):
        calculate(CalcInput(finish_type="matte", target_coverage=-1), catalog)


def test_calculate_wholesale_pricing_when_set():
    y = _yield(price_retail=Decimal("100"), price_wholesale=Decimal("70"))
    catalog = _catalog([y], [_config()])
    result = calculate(
        CalcInput(finish_type="matte", target_coverage=100.0, pricing_tier="wholesale"),
        catalog,
    )
    assert result.sections[0].items[0].unit_price == Decimal("70")


def test_strip_prices_zeros_out_costs_but_keeps_quantities():
    catalog = _catalog([_yield()], [_config()])
    result = calculate(CalcInput(finish_type="matte", target_coverage=100.0), catalog)
    redacted = strip_prices(result)
    item = redacted.sections[0].items[0]
    assert item.unit_price == Decimal("0")
    assert item.line_total == Decimal("0")
    assert item.units_required == 1   # unchanged
    assert redacted.subtotal == Decimal("0")
