# Bugs, How We Found Them, How We Fixed Them

## Reported symptoms (ASSIGNMENT.md)

- **Sunset (Client A):** March revenue doesn't match their internal records.
- **Ocean (Client B):** sometimes, after a refresh, they see revenue that looks like another company's.
- **Finance:** some totals are off by a few cents.

## How we investigated

1. Traced one dashboard request end to end: `dashboard.py` → `cache.py` → `reservations.py` → `database_pool.py`.
2. Read `seed.sql` for planted edge cases: `prop-001` exists in both tenants, one booking falls on Feb 29 23:30 UTC, and three bookings are `333.333 / 333.333 / 333.334`.
3. Mapped each symptom to the code path that could cause it.
4. Verified with `curl` against the API for both tenants, using `docker compose logs backend` to read each traceback.

## Commits

| # | Commit message | Files |
|---|---|---|
| 1 | Fixed bug - cross-tenant data leak: scope revenue cache key by tenant_id | `services/cache.py` |
| 2 | Fixed bug - DB pool never connected, so dashboard silently returned mock revenue data | `core/database_pool.py`, `services/reservations.py` |
| 3 | Fixed bug - monthly revenue used UTC month boundaries instead of the property's time zone | `services/reservations.py` |
| 4 | Fixed bug - revenue totals off by cents: round with Decimal instead of float | `api/v1/dashboard.py` |
| 5 | Fixed bug - add sqlalchemy[asyncio] (greenlet) so the async DB engine can run | `requirements.txt` |

---

## Bug 1: Cross-tenant data leak through the cache (Ocean's complaint)

**Symptom:** after a refresh, a client sometimes sees another company's revenue.

**How we found it:** the cache key in `services/cache.py` was `revenue:{property_id}`. The tenant wasn't part of it. The schema allows the same property ID in two tenants, and the seed data does exactly that with `prop-001`.

**Root cause:** whichever tenant loaded `prop-001` first filled the cache. For the next 5 minutes, the other tenant got that cached result. The SQL query was scoped by tenant, but the cache sat in front of it and skipped the check. That's why it only happened "sometimes": it depended on who loaded first and whether the entry had expired.

**Fix:**
```python
cache_key = f"revenue:{tenant_id}:{property_id}"
```

**How to demo:** flush Redis, request `prop-001` as Ocean, then as Sunset. Before the fix, Sunset gets Ocean's cached response. After the fix, Sunset gets its own 2250.00.

---

## Bug 2: The dashboard never read the database (the main reason Sunset's numbers were wrong)

**Symptom:** numbers didn't match the client's records.

**How we found it:** tracing `calculate_total_revenue` showed a `try/except` that returns a hardcoded `mock_data` dict on any exception. Checking why the `try` would fail turned up three separate errors in `database_pool.py`:

- The URL was built from `settings.supabase_db_user/host/...`, which don't exist on `Settings`, so it raised `AttributeError`. The real setting is `database_url` (from `DATABASE_URL` in docker-compose).
- `poolclass=QueuePool` can't be used with an async engine. It needs the async queue pool, which is the default.
- `get_session` was `async def`, so `async with db_pool.get_session()` got a coroutine instead of a session.

**Root cause:** every request failed silently and fell through to mock data. For `prop-001`, the mock said 1000.00 from 3 bookings. The real Sunset total is 2250.00 from 4 bookings.

**Fix:**
- Build the URL from `settings.database_url` with the `postgresql+asyncpg://` driver.
- Drop `QueuePool` and make `get_session` a normal function.
- Use one shared pool instead of creating a new engine on every request.
- Remove the mock fallback, so a DB failure returns an error instead of fake numbers shown to a client.

## Bug 5 (found during testing): Missing `greenlet` dependency

**How we found it:** once the mock fallback was gone, the endpoint returned 500 and the logs showed `ImportError: The SQLAlchemy asyncio module requires ... 'greenlet'`. The mock fallback had been hiding this too.

**Fix:** `sqlalchemy>=2.0.0` → `sqlalchemy[asyncio]>=2.0.0` in `requirements.txt`, then rebuild the backend image.

---

## Bug 3: Time zone boundaries (Sunset's March total)

**Symptom:** Sunset's March total differs from their records.

**How we found it:** `res-tz-1` checks in at `2024-02-29 23:30 UTC`. Sunset's property is in `Europe/Paris` (UTC+1), where that moment is **March 1, 00:30**. `calculate_monthly_revenue` built its month range with naive `datetime(year, month, 1)`, which the database treats as UTC, so the booking landed in February.

**Root cause:** month boundaries have to be in the property's local time. The client books and reports in Paris time, not UTC.

**Fix:** look up `properties.timezone` for `(property_id, tenant_id)` and build `start`/`end` with `ZoneInfo(tz)`. The query compares those against `TIMESTAMPTZ` values, so Postgres converts correctly. The function now also takes `tenant_id`, so it can't read another tenant's rows.

**Result:** Sunset prop-001 March 2024 = 2250.000 (4 bookings). February = 0.

---

## Bug 4: Totals off by a few cents (finance's complaint)

**Symptom:** some totals are off by a cent here and there.

**How we found it:** the database stores `NUMERIC(10,3)` (exact, three decimals). `dashboard.py` converted that with `float(...)`, and the frontend rounded it with `Math.round(x * 100) / 100`.

**Root cause:** floats are binary and can't represent most decimal amounts exactly. Rounding a float to cents can go the wrong way on half-cent values. For example, in JavaScript `Math.round(1.005 * 100) / 100` gives `1`, because `1.005 * 100` is `100.49999...`. With amounts like `333.333` in the data, drift shows up in some totals and not others.

**Fix:** round on the backend with exact decimal math before anything becomes a float:
```python
total = Decimal(revenue_data["total"]).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```
The service keeps full precision as a string internally and rounds once, at the API edge.

---

## Verified results (after all fixes)

| Login | Property | total_revenue | reservations_count |
|---|---|---|---|
| Ocean | prop-001 | 0.00 | 0 |
| Sunset | prop-001 | 2250.00 | 4 |
| Sunset | prop-002 | 4975.50 | 4 |
| Sunset | prop-003 | 6100.50 | 2 |
| Ocean | prop-004 | 1776.50 | 4 |
| Ocean | prop-005 | 3256.00 | 3 |

These match a direct `SUM(total_amount)` query grouped by tenant and property in Postgres.

## Environment issue (not a code bug, not committed)

The Codespace's Docker setup dropped container-to-container traffic, so the backend timed out reaching both Redis and Postgres even though DNS and networks were correct. We worked around it with an untracked `docker-compose.override.yml` that routes through the Docker gateway (`172.18.0.1`) using the published ports. In a normal Docker environment this isn't needed.

## Not fixed, worth mentioning in the video

- **Dropdown shows everything:** it lists all five properties to both tenants. It should load only the logged-in tenant's properties from the API.
- **RLS has no policies:** Postgres row-level security is enabled but no policies are defined. Adding them would give a database-level backstop for tenant isolation.
- **Currency is hardcoded:** it's always `"USD"`, even though reservations have a `currency` column.
- **Monthly revenue isn't exposed:** `calculate_monthly_revenue` isn't wired to an endpoint. The dashboard shows all-time totals.
