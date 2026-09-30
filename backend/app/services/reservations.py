from datetime import datetime
from decimal import Decimal
from typing import Dict, Any
from zoneinfo import ZoneInfo

from sqlalchemy import text

from app.core.database_pool import db_pool


async def _ensure_pool():
    # Reuse one shared pool instead of building a new engine on every request
    if db_pool.session_factory is None:
        await db_pool.initialize()
    if db_pool.session_factory is None:
        raise RuntimeError("Database pool not available")


async def calculate_monthly_revenue(property_id: str, tenant_id: str, month: int, year: int) -> Decimal:
    """
    Revenue for a calendar month in the PROPERTY'S local time zone.
    A check-in at 2024-02-29 23:30 UTC is 2024-03-01 00:30 in Paris, so it belongs to March.
    """
    await _ensure_pool()
    async with db_pool.get_session() as session:
        tz_row = (await session.execute(
            text("SELECT timezone FROM properties WHERE id = :pid AND tenant_id = :tid"),
            {"pid": property_id, "tid": tenant_id},
        )).fetchone()
        tz = ZoneInfo(tz_row.timezone if tz_row else "UTC")

        start = datetime(year, month, 1, tzinfo=tz)
        end = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)

        total = (await session.execute(
            text("""
                SELECT COALESCE(SUM(total_amount), 0)
                FROM reservations
                WHERE property_id = :pid AND tenant_id = :tid
                  AND check_in_date >= :start AND check_in_date < :end
            """),
            {"pid": property_id, "tid": tenant_id, "start": start, "end": end},
        )).scalar()
        return Decimal(str(total))


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
