"""Generic quote/estimate calculator engine for SIA Module B.

The calculator reads per-client product yields and finish configs from
Supabase and produces a quote of the form:

    sections:
      - finish: <finish_type>
        items:
          - product: <name>
            sku_size: <size>
            unit_price: <retail or wholesale>
            coverage_per_unit: <number>
            units_required: <ceil(target_coverage / coverage_per_unit)>
            line_total: <unit_price * units_required>
        addons:
          - <same shape, for each required addon>
    summary:
      subtotal: <number>
      currency: USD

The math: for each requested finish, find the matching product_yields
row by (finish_group, product_category, pack_size), divide target
coverage by per-unit yield (rounded up), multiply by retail price.
For category 'small'/'large' pack sizes, prefer the smallest pack count
that satisfies coverage — a pack-size optimizer is a near-term follow-up.

Per-client tenant isolation is enforced by RLS on product_yields and
calculator_configs. Callers must have tenant context set.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from app.db import get_connection


# ---------------------------------------------------------------------------
# Pure data structures — engine is testable without DB
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ProductYield:
    id: int
    product_name: str
    sku_size: str
    price_retail: Decimal
    price_wholesale: Decimal | None
    coverage_per_unit: Decimal | None
    coverage_unit: str
    finish_group: str
    product_category: str
    pack_size: str
    notes: str | None = None


@dataclass(frozen=True)
class FinishConfig:
    finish_type: str
    finish_group: str
    display_name: str
    required_addons: list[str]      # product_category strings the engine auto-adds
    sort_order: int = 0


@dataclass
class Catalog:
    """In-memory bundle of yields + configs. Built once per calc call."""

    yields_by_group: dict[str, list[ProductYield]]
    configs_by_type: dict[str, FinishConfig]

    def yields_for(self, finish_group: str, category: str) -> list[ProductYield]:
        return [
            y for y in (self.yields_by_group.get(finish_group, []) + self.yields_by_group.get("all", []))
            if y.product_category == category
        ]


@dataclass
class CalcInput:
    """Input for a quote.

    finish_type: one of calculator_configs.finish_type values for this client
    target_coverage: sqft (or whatever unit the products use)
    pricing_tier: 'retail' or 'wholesale'
    """

    finish_type: str
    target_coverage: float
    pricing_tier: str = "retail"


@dataclass
class LineItem:
    product_name: str
    sku_size: str
    unit_price: Decimal
    units_required: int
    line_total: Decimal
    coverage_per_unit: Decimal | None
    coverage_unit: str


@dataclass
class Section:
    finish: str
    items: list[LineItem] = field(default_factory=list)
    addons: list[LineItem] = field(default_factory=list)


@dataclass
class CalcResult:
    sections: list[Section]
    subtotal: Decimal
    currency: str = "USD"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

async def load_catalog(client_id: UUID) -> Catalog:
    """Build the per-client catalog from Supabase. Honors tenant RLS."""
    del client_id  # unused; the active tenant context enforces scope via RLS
    async with get_connection() as conn:
        yield_rows = await conn.fetch(
            """
            SELECT id, product_name, sku_size, price_retail, price_wholesale,
                   coverage_per_unit, coverage_unit, finish_group, product_category,
                   pack_size, notes
            FROM product_yields
            """
        )
        config_rows = await conn.fetch(
            """
            SELECT finish_type, finish_group, display_name, required_addons, sort_order
            FROM calculator_configs
            ORDER BY sort_order
            """
        )

    yields_by_group: dict[str, list[ProductYield]] = {}
    for r in yield_rows:
        y = ProductYield(
            id=r["id"],
            product_name=r["product_name"],
            sku_size=r["sku_size"],
            price_retail=Decimal(str(r["price_retail"])),
            price_wholesale=Decimal(str(r["price_wholesale"])) if r["price_wholesale"] is not None else None,
            coverage_per_unit=Decimal(str(r["coverage_per_unit"])) if r["coverage_per_unit"] is not None else None,
            coverage_unit=r["coverage_unit"],
            finish_group=r["finish_group"],
            product_category=r["product_category"],
            pack_size=r["pack_size"],
            notes=r["notes"],
        )
        yields_by_group.setdefault(y.finish_group, []).append(y)

    configs_by_type = {
        r["finish_type"]: FinishConfig(
            finish_type=r["finish_type"],
            finish_group=r["finish_group"],
            display_name=r["display_name"],
            required_addons=list(r["required_addons"] or []),
            sort_order=r["sort_order"],
        )
        for r in config_rows
    }

    return Catalog(yields_by_group=yields_by_group, configs_by_type=configs_by_type)


# ---------------------------------------------------------------------------
# Pure engine — no DB, easy to test
# ---------------------------------------------------------------------------

def calculate(input: CalcInput, catalog: Catalog) -> CalcResult:
    """Run the calculator against a loaded catalog.

    Raises ValueError when input is unworkable (unknown finish, missing
    base product, etc.) so the caller can return a clean 422.
    """
    if input.target_coverage <= 0:
        raise ValueError("target_coverage must be > 0")

    config = catalog.configs_by_type.get(input.finish_type)
    if config is None:
        raise ValueError(f"unknown finish_type: {input.finish_type}")

    target = Decimal(str(input.target_coverage))
    section = Section(finish=config.display_name)

    # Base products: pick from the finish's group. The first category we
    # find drives the headline line; addons fill in the rest. The category
    # convention is per-client: typical patterns are 'base', 'liquid',
    # 'topcoat', etc.
    base_category = _infer_base_category(catalog, config.finish_group)
    if base_category is None:
        raise ValueError(
            f"no base product defined for finish_group={config.finish_group}"
        )

    base_yields = catalog.yields_for(config.finish_group, base_category)
    if not base_yields:
        raise ValueError(
            f"no products for (finish_group={config.finish_group}, category={base_category})"
        )

    section.items.extend(_size_to_target(base_yields, target, input.pricing_tier))

    # Addons: same shape, one per required category
    for addon_category in config.required_addons:
        addon_yields = catalog.yields_for(config.finish_group, addon_category)
        if not addon_yields:
            # Skip silently — required addon not yet seeded for this client
            continue
        section.addons.extend(_size_to_target(addon_yields, target, input.pricing_tier))

    subtotal = sum(
        (li.line_total for li in section.items + section.addons),
        start=Decimal("0"),
    )

    return CalcResult(sections=[section], subtotal=subtotal)


def strip_prices(result: CalcResult) -> CalcResult:
    """Zero out prices for users who lack the can_see_prices permission.

    Quantities and product information remain so the customer-facing quote
    is still useful as a materials list.
    """
    zero = Decimal("0")
    redacted_sections = []
    for s in result.sections:
        new_items = [
            LineItem(
                product_name=li.product_name,
                sku_size=li.sku_size,
                unit_price=zero,
                units_required=li.units_required,
                line_total=zero,
                coverage_per_unit=li.coverage_per_unit,
                coverage_unit=li.coverage_unit,
            )
            for li in s.items
        ]
        new_addons = [
            LineItem(
                product_name=li.product_name,
                sku_size=li.sku_size,
                unit_price=zero,
                units_required=li.units_required,
                line_total=zero,
                coverage_per_unit=li.coverage_per_unit,
                coverage_unit=li.coverage_unit,
            )
            for li in s.addons
        ]
        redacted_sections.append(Section(finish=s.finish, items=new_items, addons=new_addons))

    return CalcResult(sections=redacted_sections, subtotal=zero)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _infer_base_category(catalog: Catalog, finish_group: str) -> str | None:
    """Heuristic: the most-rows category in this finish_group is the base.

    Replace with an explicit `is_base` flag in calculator_configs if the
    heuristic ever picks wrong. The pattern works for typical materials
    catalogs where the bulk product has the most pack-size variants and
    addons have fewer.
    """
    yields = catalog.yields_by_group.get(finish_group, []) + catalog.yields_by_group.get("all", [])
    if not yields:
        return None
    counts: dict[str, int] = {}
    for y in yields:
        counts[y.product_category] = counts.get(y.product_category, 0) + 1
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _size_to_target(
    yields: list[ProductYield],
    target_coverage: Decimal,
    pricing_tier: str,
) -> list[LineItem]:
    """Pick pack sizes to cover the target, preferring larger packs first.

    For each pack size with a per-unit yield, compute units = ceil(target / per_unit)
    and emit a LineItem. Products without a coverage_per_unit (e.g. activators
    that depend on user choice) are skipped here — the caller will add them
    separately if needed.

    Real packing optimizer (smallest-cost combination of small + large packs)
    is a near-term follow-up; this version emits one row per available pack
    size sufficient to cover the target alone.
    """
    items: list[LineItem] = []
    seen_packs: set[tuple[str, str]] = set()

    # Sort: large packs first — usually more cost-efficient per sqft
    sorted_yields = sorted(yields, key=lambda y: 0 if y.pack_size == "large" else 1)

    for y in sorted_yields:
        if y.coverage_per_unit is None or y.coverage_per_unit <= 0:
            continue
        key = (y.product_name, y.pack_size)
        if key in seen_packs:
            continue
        seen_packs.add(key)

        units = math.ceil(target_coverage / y.coverage_per_unit)
        if units <= 0:
            continue

        unit_price = _price_for_tier(y, pricing_tier)
        items.append(
            LineItem(
                product_name=y.product_name,
                sku_size=y.sku_size,
                unit_price=unit_price,
                units_required=units,
                line_total=unit_price * units,
                coverage_per_unit=y.coverage_per_unit,
                coverage_unit=y.coverage_unit,
            )
        )

        # Once we have a valid pack size for this product, we're done with it.
        # Subsequent pack sizes of the same product are alternatives, not additions.
        # The pack optimizer (TBD) would weigh them; for now we take the largest.
        break

    return items


def _price_for_tier(y: ProductYield, tier: str) -> Decimal:
    if tier == "wholesale" and y.price_wholesale is not None:
        return y.price_wholesale
    return y.price_retail
