"""Quote/estimate calculator endpoints (SIA Module B).

Per-client product catalogs live in product_yields + calculator_configs.
RLS keeps catalogs isolated. The engine is generic — the same code
serves a flooring contractor and a pool resurfacer; only the seeded
data differs.

Endpoints require can_use_calculator (default true). Users without
can_see_prices receive a zeroed-out price column.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.db import get_connection, get_current_tenant
from app.middleware.auth import AuthUser, verify_jwt
from app.services.calculator import CalcInput, CalcResult, calculate, load_catalog, strip_prices
from app.services.permissions import get_user_permissions

router = APIRouter(
    prefix="/api/calculator",
    tags=["calculator"],
    dependencies=[Depends(verify_jwt)],
)


@router.post("/estimate")
async def estimate(
    input: CalcInput,
    user: AuthUser = Depends(verify_jwt),
) -> dict[str, Any]:
    """Run the calculator for the current tenant.

    Returns sections + summary. Strips prices for users who lack
    can_see_prices.
    """
    client_id = get_current_tenant()
    if client_id is None:
        raise HTTPException(status_code=400, detail="missing tenant context")

    catalog = await load_catalog(client_id)
    if not catalog.configs_by_type:
        raise HTTPException(
            status_code=500,
            detail="calculator not configured for this tenant — seed product_yields + calculator_configs",
        )

    try:
        result = calculate(input, catalog)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    perms = await get_user_permissions(user.user_id)
    if not perms.can_view_leads and not perms.is_admin:
        # Reuse can_view_leads as the proxy for can_see_prices until that
        # flag is added explicitly to UserPermissions. Conservative default.
        result = strip_prices(result)

    return _result_to_dict(result)


@router.get("/options")
async def options() -> dict[str, Any]:
    """Return finish + category dropdown data for the calculator UI."""
    async with get_connection() as conn:
        configs = await conn.fetch(
            """
            SELECT finish_type, display_name, sort_order
            FROM calculator_configs
            ORDER BY sort_order
            """
        )
        categories = await conn.fetch(
            "SELECT DISTINCT product_category FROM product_yields ORDER BY product_category"
        )

    return {
        "finishes": [
            {
                "finish_type": r["finish_type"],
                "display_name": r["display_name"],
                "sort_order": r["sort_order"],
            }
            for r in configs
        ],
        "categories": [r["product_category"] for r in categories],
    }


def _result_to_dict(result: CalcResult) -> dict[str, Any]:
    """Marshal CalcResult dataclasses to JSON-friendly dicts."""

    def _line_item(li: Any) -> dict[str, Any]:
        return {
            "product_name": li.product_name,
            "sku_size": li.sku_size,
            "unit_price": _decimal_to_float(li.unit_price),
            "units_required": li.units_required,
            "line_total": _decimal_to_float(li.line_total),
            "coverage_per_unit": _decimal_to_float(li.coverage_per_unit),
            "coverage_unit": li.coverage_unit,
        }

    return {
        "sections": [
            {
                "finish": s.finish,
                "items": [_line_item(li) for li in s.items],
                "addons": [_line_item(li) for li in s.addons],
            }
            for s in result.sections
        ],
        "summary": {
            "subtotal": _decimal_to_float(result.subtotal),
            "currency": result.currency,
        },
    }


def _decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
