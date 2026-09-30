# PropertyFlow System Overview

## What this is

A multi-tenant property management dashboard. Each client company (tenant) logs in and sees revenue for its own rental properties. The take-home is a debugging exercise: the app is live, clients reported wrong and leaked numbers, and the job is to find and fix the causes.

## Where it runs (the Codespace)

- A GitHub Codespace is a cloud Linux VM running VS Code in the browser, opened on your fork (`anthonyramirez244/New_devs_App`).
- `docker compose` inside the Codespace runs four containers. Ports are forwarded so `localhost:8000` and `localhost:3000` work from the Codespace terminal and browser.

| Service | Tech | Port (host → container) | Role |
|---|---|---|---|
| `frontend` | React + Vite + TypeScript, served by nginx | 3000 → 80 | Login page and revenue dashboard |
| `backend` | Python FastAPI (uvicorn) | 8000 → 8000 | Auth, revenue API, caching |
| `db` | Postgres 15 | 5433 → 5432 | Tenants, properties, reservations |
| `redis` | Redis | 6380 → 6379 | 5-minute cache for revenue results |

Local-only workaround: this Codespace blocks container-to-container traffic, so `docker-compose.override.yml` points the backend at the Docker gateway (`172.18.0.1:5433` and `:6380`). The file is listed in `.git/info/exclude` and is never committed.

## Data model (`database/schema.sql`, `database/seed.sql`)

- `tenants`: `tenant-a` = Sunset Properties, `tenant-b` = Ocean Rentals.
- `properties`: primary key is `(id, tenant_id)`, so two tenants can both own a `prop-001`. Each property has a `timezone` (Sunset in `Europe/Paris`, Ocean in `America/New_York`).
- `reservations`: `check_in_date` is `TIMESTAMPTZ` (stored in UTC), `total_amount` is `NUMERIC(10,3)` (three decimals, sub-cent precision).

| Tenant | Properties | Bookings |
|---|---|---|
| Sunset (tenant-a) | prop-001 Beach House Alpha, prop-002, prop-003 | 10 |
| Ocean (tenant-b) | prop-001 Mountain Lodge Beta (no bookings), prop-004, prop-005 | 7 |

The seed data contains planted traps: a booking at `2024-02-29 23:30 UTC` (March 1 in Paris), three bookings of `333.333 / 333.333 / 333.334`, and the shared `prop-001` ID.

## Request flow

1. **Login**: `POST /api/v1/auth/login` (`backend/app/api/v1/login.py`). The two client accounts are hardcoded. It returns an HS256 JWT signed with `SECRET_KEY`, with `tenant_id` in `app_metadata`.
2. **Auth**: every API call runs `authenticate_request` (`core/auth.py`). It decodes the JWT, then `TenantResolver` maps the email to a tenant. The Supabase lookups for permissions and cities fail and fall back to empty lists, which is expected in this challenge.
3. **Dashboard**: the frontend (`Dashboard.tsx` → `RevenueSummary.tsx`) calls `GET /api/v1/dashboard/summary?property_id=...`.
4. **API** (`api/v1/dashboard.py`) → **cache** (`services/cache.py`, Redis, 5-minute TTL) → on a cache miss, **revenue service** (`services/reservations.py`), which queries Postgres through `core/database_pool.py` (SQLAlchemy async + asyncpg).
5. The response `{property_id, total_revenue, currency, reservations_count}` is rendered as the "Total Revenue" card.

## Files that matter for this challenge

- `backend/app/api/v1/dashboard.py`: the endpoint and the rounding
- `backend/app/services/cache.py`: Redis cache and its key
- `backend/app/services/reservations.py`: SQL for total and monthly revenue
- `backend/app/core/database_pool.py`: DB engine and sessions
- `backend/app/core/auth.py`, `core/tenant_resolver.py`, `api/v1/login.py`: who the user is and which tenant they belong to
- `frontend/src/components/RevenueSummary.tsx`, `Dashboard.tsx`: display

Most other files (Supabase clients, city access, token encryption, the SQL scripts, the committed `node_modules`) come from a larger production app and don't affect the dashboard. Treat them as noise.

## Things to know

- Multi-tenancy is enforced only in application code (`WHERE tenant_id = ...` and the cache key). Postgres RLS is enabled but has no policies, so the database gives no protection on its own.
- The frontend dropdown lists all five properties for both tenants. Backend scoping is what keeps the data separate.
- The frontend sends an `X-Simulated-Tenant` header. The backend ignores it, which is correct: tenant comes only from the verified token.
- `calculate_monthly_revenue` isn't wired to any endpoint. The dashboard shows all-time totals per property.
- `.vscode/tasks.json` includes "SSH Prod" and "SSH Staging" tasks aimed at the company's servers. Don't run them.

## Useful commands

```bash
docker compose up -d --build          # start or rebuild everything
docker compose logs backend --tail 40 # read backend errors
docker compose exec redis redis-cli FLUSHALL   # clear cache before a test
docker compose exec db psql -U postgres -d propertyflow   # SQL shell
```
