# TCI Higher Life Center — Receipt & Financial Management System

**Version:** 1.0  
**Stack:** Python 3.10+ / Django 5.2.x / PostgreSQL 18.x / Redis / Celery 5.6.x / Docker & Compose / WeasyPrint

---

## 1. Overview

The **TCI Higher Life Center Receipt & Financial Management System** is a modular monolith application designed to digitize church financial operations: contributions, unique receipt generation, disbursement tracking, evidence verification, notifications, automated audit trails, and financial reporting.

---

## 2. Core Architecture & Principles

The system strictly follows the confirmed business rules and architecture:
1. **Modular Monolith**: Separated into domain apps (`accounts`, `members`, `contributions`, `receipts`, `requisitions`, `notifications`, `audit`, `reports`, `core`).
2. **Single System Administrator**: Exactly one System Administrator exists; there is no Super Admin role.
3. **Protected Recovery**: Administrator recovery contacts (`SYSTEM_ADMIN_RECOVERY_EMAIL`, `SYSTEM_ADMIN_RECOVERY_PHONE`) are protected environment secrets.
4. **Phone-Based Contributor Matching**: Automatic digital contributions match members strictly by transaction phone number.
5. **Separation of Amounts**: Requested, approved, and disbursed amounts on requisitions remain separate.
6. **Receipt Numbers**: `HLC-XXXXXXX` with exactly 7 digits; collision-resistant, unique, and never reused.
7. **Receipt Immutability**: Automatic receipts are strictly immutable. Manual receipts are soft-deleted and editable only by creator subject to permissions.
8. **Requisition Lifecycle**: Rejected requisitions cannot be reopened; proof of expenditure must be verified before completion.
9. **Append-Only Audit**: Audit records cannot be modified or deleted.

---

## 3. Project Structure

```text
tci_receipt/
├── apps/
│   ├── accounts/          # Custom User, Roles, Permissions, UserRole, RolePermission
│   ├── audit/             # Append-only AuditLog and AuditService
│   ├── contributions/     # Contributions, ContributionTypes, PaymentTransactions
│   ├── core/              # UUIDBaseModel, ID Generators, StorageService abstraction, Healthcheck
│   ├── members/           # Members, MemberPhoneNumbers, TemporaryContributors, MergeRequests
│   ├── notifications/     # NotificationTemplates, Outbound Notifications, ManualActions
│   ├── receipts/          # Receipts (HLC-XXXXXXX), ReceiptEdits, DeliveryRecords
│   ├── reports/           # Financial & Operational Reports, CSV/Excel Exports
│   └── requisitions/      # Requisitions, Attachments, Assignments, Approvals, Disbursements, Evidence
├── config/
│   ├── settings/          # base.py, development.py, production.py
│   ├── celery.py          # Celery configuration
│   ├── urls.py            # URL routing
│   └── wsgi.py / asgi.py
├── docker/
│   └── entrypoint.sh      # Container entrypoint applying migrations & seed data
├── scripts/
│   └── seed_data.py       # Helper script for running idempotent database seeding
├── tests/                 # Automated test suite (accounts, members, contributions, receipts, requisitions, audit, health)
├── .env.example           # Environment template
├── Dockerfile             # Multi-stage container definition with WeasyPrint & PostgreSQL
├── docker-compose.yml     # Services: web, postgres, redis, celery_worker
├── manage.py
└── pyproject.toml         # Dependencies and pytest configuration
```

---

## 4. Quickstart Guide

### 4.1 Local Setup

1. **Activate the Virtual Environment**:
   ```bash
   source .venv/bin/activate
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env` and adjust database/redis credentials as needed:
   ```bash
   cp .env.example .env
   ```

3. **Apply Database Migrations**:
   ```bash
   python manage.py migrate
   ```

4. **Idempotently Seed Initial Data**:
   Seeds the initial roles, permissions, contribution types, the single System Administrator, and notification templates:
   ```bash
   python manage.py seed_db
   ```

5. **Run the Test Suite**:
   ```bash
   pytest
   ```

6. **Start the Development Server**:
   ```bash
   python manage.py runserver
   ```

7. **Verify Healthcheck**:
   Visit `http://localhost:8000/health/`

---

### 4.2 Docker Compose Setup

Run the complete multi-service stack with a single command:
```bash
docker compose up --build
```

Services started:
- `postgres`: PostgreSQL database with persistent volume.
- `redis`: Redis broker and cache.
- `web`: Django web application served via Gunicorn on port 8000.
- `celery_worker`: Background task worker.

---

## 5. Running Automated Tests

Run the full test suite via pytest:
```bash
pytest
```
Or for verbose output with coverage:
```bash
pytest -v
```
