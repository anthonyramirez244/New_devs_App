from datetime import datetime
from decimal import Decimal
from typing import Dict, Any

from sqlalchemy import text

from app.core.database_pool import db_pool


async def _ensure_pool():
    # Reuse one shared pool instead of building a new engine on every request
    if db_pool.session_factory is None:
        await db_pool.initialize()
    if db_pool.session_factory is None:
        raise RuntimeError("Database pool not available")


async def calculate_monthly_revenue(property_id: str, month: int, year: int, db_session=None) -> Decimal:
    """
    Calculates revenue for a specific month.
    NOTE: uses naive datetimes (no time zone). Fixed in the next commit.
    """
    start_date = datetime(year, month, 1)
    if month < 12:
        end_date = datetime(year, month + 1, 1)
    else:
        end_date = datetime(year + 1, 1, 1)
    return Decimal('0')  # Placeholder in original code


async def calculate_total_revenue(property_id: str, tenant_id: str) -> Dict[str, Any]:
    """
    Aggregates revenue from the database, scoped to the tenant.
    No silent mock-data fallback: if the DB is down we fail loudly instead of
    showing made-up numbers to clients.
    """
    await _ensure_pool()
    async with db_pool.get_session() as session:
        row = (await session.execute(
            text("""
                SELECT SUM(total_amount) AS total_revenue, COUNT(*) AS reservation_count
                FROM reservations
                WHERE property_id = :property_id AND tenant_id = :tenant_id
            """),
            {"property_id": property_id, "tenant_id": tenant_id},
        )).fetchone()

    total = Decimal(str(row.total_revenue)) if row and row.total_revenue is not None else Decimal("0")
    return {
        "property_id": property_id,
        "tenant_id": tenant_id,
        "total": str(total),  # keep full precision as a string; round only at the edge
        "currency": "USD",
        "count": row.reservation_count if row else 0,
    }
