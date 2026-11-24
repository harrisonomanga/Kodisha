# Rental Management Platform: build 0.1 (foundation)

Backend foundation for the platform described in the BRD (v0.2) and the System Design doc (v0.1).
Django 5.2 modular monolith, PostgreSQL in production (SQLite for local dev), Celery for background jobs.

## Run it

    pip install -r requirements.txt
    python manage.py migrate
    python manage.py createsuperuser
    python manage.py seed_demo            # org, property, 5 units, 3 tenants, M-Pesa webhook URL
    python manage.py generate_invoices --org 1 --date 2026-11-02
    python manage.py simulate_mpesa --org 1 --account PLTA11 --amount 15000.00
    python manage.py runserver            # /admin/ to look around
    python manage.py test                 # 35 tests

Set `POSTGRES_DB` (see `.env.example`) to use PostgreSQL. `CELERY_EAGER=1` (default) runs jobs inline;
use `0` with Redis for staging and production.

## What is built

| Area | BRD refs | Status |
|---|---|---|
| Organisations, memberships, roles, property-level scope (data model only) | FR-ACC-02/03/04 | Models done; **no login or API enforcement yet** |
| Append-only audit log | FR-ACC-06 | Done |
| Properties, units, tenants, leases, one active lease per unit | FR-PROP-01/02, FR-TEN-01/02 | Done (admin UI) |
| Unit account numbers with check character | FR-PROP-04, section 8 | Done |
| Immutable double-entry ledger, idempotent posting, reversals | sections 7, 13 | Done |
| Recurring invoices, recurring charges, pro-rata, idempotent generation | FR-BIL-01/02/05 | Done |
| Credit from overpayment, applied automatically to next invoice | FR-BIL-09 | Done |
| Payment pipeline: raw store, duplicate protection, matching, oldest-first allocation | FR-PAY-01/02/04/05/06 | Done |
| M-Pesa C2B confirmation webhook (store, acknowledge, queue) | FR-PAY-02 | Done, tested with simulated payloads only |
| Review queue for unmatched / ambiguous / no-lease payments, with suggestions | FR-PAY-07 | Done (model, services, command); UI pending |
| Bank statement import (CSV, per-bank column profiles, dedupe on re-upload) | FR-PAY-08 | Done |
| Manual payments with unique proof reference | FR-PAY-09 | Done; approval rules pending |
| Reversals / refunds as ledger entries | FR-PAY-12 | Done |
| Arrears ageing report | FR-REP-02 | First cut (function, no UI) |
| Replay tool for stored-but-unprocessed events | section 8.7 | Done |

## Not built yet (deliberately)

REST API and the React web app (Django admin is the interim UI), login (OTP) and 2FA, PostgreSQL row-level
security, encrypted storage of channel credentials and ID numbers, SMS and reminders, receipts and PDFs,
daily reconciliation report, bulk import of properties and tenants, deposits, move-out statement, credit notes,
metered utilities, penalties, landlord statements and commission, Airtel Money, USSD, monitoring, CI/CD.

## Things to know

* Money is stored as integer cents. `1_500_000` = KES 15,000.00.
* **PostgreSQL triggers** that block UPDATE and DELETE on the ledger and audit tables are written
  (`ledger/migrations/0002`) but only the SQLite path (no-op) has run here. First job on a real Postgres:
  run the migration and confirm the triggers fire.
* The M-Pesa payload format follows Daraja's C2B confirmation fields. It has **not** been tested against Safaricom.
* A payment whose credit was already applied to a later invoice, then reversed, leaves the tenant's credit
  negative. That is economically right but needs a policy (see question 9).

## Layout

    core/       organisations, memberships, audit log, append-only base classes
    portfolio/  landlords, properties, units, tenants, leases, account numbers
    ledger/     accounts, journals, entries, posting and reversal services
    billing/    charge types, invoices, allocations, invoice generation, arrears report
    payments/   channel config, raw events, payments, matching, adapters, webhook, bank import, review queue

## Decisions I made that you can overrule

1. A rent invoice for a month is **due on the lease's billing day in that month**, and is generated
   `invoice_lead_days` (default 5) before the due date.
2. Pro-rata uses actual days in the month and applies to **rent only**; recurring charges bill in full.
3. Payments go to the **oldest open invoice first**; any remainder becomes tenant credit.
4. A payment auto-matches only on an **exact** account number (after ignoring case, spaces, hyphens).
   A near miss (one character off) is only ever a suggestion.
5. On bank statements, an account number found as a whole word in the **narration** also counts as an
   exact match (see question 3).

## Open questions for us

1. **Rent timing.** Is rent due in advance on the 1st, or on each tenant's own billing day? Is there a
   grace period before it counts as arrears?
2. **Pro-rata.** Should service charges and utilities also be pro-rated on a part month?
3. **Bank narration matching.** Are you comfortable auto-matching when the exact account number appears
   anywhere in the narration, or should bank lines always go to review unless the reference field matches?
4. **Typed prefixes.** Do tenants add prefixes or extra text to the account number (for example "ACC")?
   Tell me the common ones and I will strip them.
5. **Manual payments.** Who may record them, and above what amount does a second person have to approve?
6. **Hosting and secrets.** Where will this run (data residency)? That decides how channel credentials
   (Daraja keys) are encrypted, which I have not built yet.
7. **Login.** SMS one-time passcodes need an SMS provider. Do you have one in mind, or should I pick on price?
8. **Daraja access.** Do you have, or can you get, a sandbox app and one pilot Paybill? I can only prove the
   M-Pesa path against Safaricom with that.
9. **Reversal policy.** If a payment is reversed after its credit was already used on a later invoice, should
   we re-open that invoice, or carry the amount as a debt on the tenant?
10. **Front end.** Happy with a React PWA on top of a REST API next, or would you rather I extend the admin
    for the pilot and delay the front end?
