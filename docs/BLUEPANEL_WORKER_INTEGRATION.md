# BluePanel Worker study and safe PGClockBot integration

Reviewed user-provided **worker1.js** (BLUEPANEL_CORE_WORKER v5.0.92),
**worker2.js** (BLUEPANEL_EDGE_WORKER v5.0.93), and
**worker3.js** (BLUEPANEL_PROCESSOR_WORKER v5.0.93).

These sources use JavaScript Cloudflare Workers, D1, Cron, service bindings and
reseller-bot-specific tables. PGClockBot uses Python/asyncio, FastAPI,
SQLAlchemy, Telegram aiogram and PasarGuard/GoGuard adapters. The source files
are **reference material**, not an executable drop-in dependency.

## Ported in PGClockBot v0.5.2

| Worker pattern | PGClockBot implementation | Safety boundary |
| --- | --- | --- |
| Core `bluePanelIsTransientPasarguardError` + `bluePanelWithD1Retry`: distinguish temporary provider/network failures and bounded backoff | `app/services/panel_retry.py`, `app/services/pg_read_resilience.py` | At most **one** retry for GET in background/bot. No POST/PUT/PATCH/DELETE retry; no retry inside web-panel budget. `429 Retry-After` over 1 second is honored by **not retrying**. |
| Processor `isTransientProviderSyncError` / `isServiceHealthTransientError`: classify provider outages instead of confusing them with auth failures | `classify_provider_failure`, `scripts/check_panel_provider.py` | Only generic error category printed, never credentials, response body or subscription URL |
| Processor `serviceHealthScore`: health/degradation bands by latency | `scripts/check_panel_provider.py` | Read-only diagnostic; slow requests are not treated as verified downtime |
| Core v5.0.92: PasarGuard migration workflow with preflight / baseline planning | `scripts/preview_provider_migration.py` | Read-only local audit only. No user migration, no panel mutations, no synthetic service IDs |

## Already implemented — not duplicated

- Worker Edge `enforceSalesSecurity`: PGClockBot has rate limits, session
  security, shop isolation and idempotent payment/settlement paths.
- Worker Edge `serviceUsageDelta`: PGClockBot PAYG billing already uses durable
  usage watermarks, reset detection and idempotency keys
  (`app/services/billing.py`). The D1 cursor tables are not copied.
- Worker Core/Processor report topics and outbox: PGClockBot already has
  tenant-aware reporting and Telegram notification jobs. A full forum-topic
  rewrite would require additional product requirements and migrations.
- Worker Processor `processPaidSalesOrderInvoices`: PGClockBot already has
  atomic paid/delivering claims, stale order manual resolution and settlement
  idempotency. Automatic re-provisioning of an uncertain paid order remains
  deliberately disabled to prevent duplicate customer accounts.
- D1 database-full maintenance and Worker Core/Edge/Processor bindings:
  Cloudflare-specific and **not applicable** to PGClockBot's SQLAlchemy setup.

## Operational checks

Read-only provider diagnostics:
```bash
cd ~/PGClockBot
.venv/bin/python scripts/check_panel_provider.py
```

Safe preflight before converting PasarGuard subscriptions to GoGuard:
```bash
cd ~/PGClockBot
.venv/bin/python scripts/preview_provider_migration.py
```

A zero-exit result for the migration preflight is **not** proof that remote
GoGuard service IDs match local plan IDs. Map every active plan against the
destination panel manually and migrate existing user-service affiliations
before changing provider. In a mixed-provider installation PGClockBot still
supports **only one active panel provider at a time**.

## Tests

```bash
python -m pytest tests/test_bluepanel_worker_resilience.py tests/test_bluepanel_migration_dry_run.py -q
```

The uploaded Worker source files are **not** committed to this repository, and
no Cloudflare D1 tables, tokens or webhooks are imported.
