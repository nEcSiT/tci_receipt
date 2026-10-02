# TCI HIGHER LIFE CENTER
# RECEIPT & FINANCIAL MANAGEMENT SYSTEM
## Engineering Implementation Specification — Coding AI Handoff

**Version:** 1.0  
**Status:** Implementation Baseline  
**Date:** September 2026

## 1. PURPOSE

This document is the implementation handoff for building the TCI Higher Life Center Receipt & Financial Management System. Treat it as the engineering source of truth for the approved requirements and logical database model.

Build the system as a production-oriented financial/administrative application, not as a CRUD demo. Prioritize accuracy, traceability, security, historical integrity, transactional safety and idempotency.

Do not silently remove, simplify or reinterpret confirmed business rules.

---

# 2. APPROVED STACK

- Python 3.11 (container baseline)
- Django 5.2.x
- PostgreSQL 17.x
- Django ORM
- Django Templates + HTML/CSS/JavaScript
- HTMX 2.x
- Celery 5.6.x
- Redis
- WeasyPrint
- Gunicorn
- Nginx
- Docker / Docker Compose
- Git / GitHub
- Modular monolith

Production uploaded files and generated PDFs should use an object/external storage abstraction. PostgreSQL stores metadata and storage keys.

---

# 3. ARCHITECTURE

Use a modular Django monolith:

```text
apps/
  accounts/
  members/
  contributions/
  receipts/
  requisitions/
  notifications/
  reports/
  audit/
  core/

config/
  settings/
  urls.py
  celery.py
  asgi.py
  wsgi.py

templates/
static/
tests/
scripts/
docker/
```

Business logic must be implemented in services rather than being concentrated in views.

Recommended services include:

```text
ContributionService
PaymentProcessingService
ContributorIdentificationService
ReceiptService
NotificationService
RequisitionAssignmentService
RequisitionApprovalService
DisbursementService
EvidenceVerificationService
AuditService
```

Models enforce local invariants; services enforce workflow/business rules; PostgreSQL enforces database-level invariants.

---

# 4. NON-NEGOTIABLE BUSINESS PRINCIPLES

1. Exactly one System Administrator exists.
2. There is no Super Admin role.
3. System Users receive configurable roles and permissions.
4. Permissions are never hardcoded to named individuals.
5. A System User cannot create a System Administrator.
6. Administrator recovery contacts are protected deployment secrets, not source-code constants.
7. Automatic contributor identification is strictly by transaction phone number.
8. Provider account name, name similarity, email or provider identity must not override phone matching.
9. A member may have multiple phone numbers.
10. An active phone cannot silently belong to multiple members.
11. Unknown automatic contributors become temporary/unverified contributors; successful transactions still proceed.
12. Contribution IDs and receipt numbers are separate identifiers.
13. Receipt numbers use `HLC-` plus exactly 7 digits and are never reused.
14. Automatic receipts are immutable, including to the System Administrator.
15. Manual receipt edits are creator-only, subject to permission, and audited.
16. Contributor phone numbers are never displayed on receipts.
17. Deleted manual receipts are soft-deleted and remain auditable.
18. Financial records are not physically deleted.
19. Notification failure never reverses a financial/admin event.
20. Requested, approved and disbursed requisition amounts remain separate.
21. Preferred and actual disbursement methods remain separate.
22. Approval does not equal disbursement.
23. Disbursement does not equal requisition completion.
24. Proof must be verified before closure.
25. Rejected requisitions cannot be edited or resubmitted; a new requisition is required.
26. Audit records are append-only during normal application operation.
27. External provider callbacks must be idempotent.
28. Important historical states must never be silently overwritten.

---

# 5. USER / AUTHENTICATION MODEL

## System Administrator

Highest-level application user. Responsibilities include:

- create/deactivate System Users
- configure roles/permissions
- system-wide review
- reports
- audit review
- exception review
- authorized reassignment
- administrator recovery

## System User

Internal user whose access is determined by assigned roles/permissions.

## External Requester

Uses the requisition portal. Does not need an internal System User account.

Every System User requires a working email.

Password reset must use secure, expiring, single-use credentials.

Deactivated users cannot log in but remain historically referenced.

## Administrator recovery

Use environment/deployment secrets:

```env
SYSTEM_ADMIN_RECOVERY_EMAIL=
SYSTEM_ADMIN_RECOVERY_PHONE=
```

Never hardcode these in source code or expose them in the frontend. OTPs are generated dynamically and expire.

Recommended flow:

```text
Recovery request
→ email OTP
→ email verification
→ phone OTP
→ phone verification
→ recovery completed
```

Audit all recovery events.

---

# 6. POSTGRESQL DATA MODEL

Use UUID primary keys, `TIMESTAMPTZ` timestamps, `NUMERIC(12,2)` monetary fields and `JSONB` for flexible provider/audit payloads where appropriate.

## 6.1 users

```text
id UUID PK
email VARCHAR(255) UNIQUE NOT NULL
password_hash TEXT NOT NULL
first_name VARCHAR(100) NOT NULL
last_name VARCHAR(100) NOT NULL
is_active BOOLEAN DEFAULT TRUE
last_login_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

## 6.2 roles

```text
id UUID PK
name VARCHAR(100) UNIQUE NOT NULL
description TEXT NULL
is_active BOOLEAN DEFAULT TRUE
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Initial roles:

```text
System Administrator
System User
```

## 6.3 permissions

```text
id UUID PK
code VARCHAR(150) UNIQUE NOT NULL
name VARCHAR(150) NOT NULL
description TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

Examples:

```text
contribution.create
contribution.edit_own
contribution.delete_own
receipt.view_own
receipt.edit_own
receipt.delete_own
member.create
member.edit
member.merge.request
member.merge.approve
requisition.view
requisition.review
requisition.approve
requisition.disburse
evidence.verify
report.view
report.export
user.manage
audit.view
```

## 6.4 user_roles

```text
user_id UUID FK users
role_id UUID FK roles
assigned_at TIMESTAMPTZ NOT NULL
assigned_by UUID FK users NULL
PK(user_id, role_id)
```

## 6.5 role_permissions

```text
role_id UUID FK roles
permission_id UUID FK permissions
assigned_at TIMESTAMPTZ NOT NULL
assigned_by UUID FK users NULL
PK(role_id, permission_id)
```

---

# 7. MEMBERS

## members

```text
id UUID PK
member_number VARCHAR(50) UNIQUE NOT NULL
first_name VARCHAR(100) NOT NULL
middle_name VARCHAR(100) NULL
last_name VARCHAR(100) NOT NULL
email VARCHAR(255) NULL
ministry VARCHAR(150) NULL
team VARCHAR(150) NULL
department VARCHAR(150) NULL
status VARCHAR(30) NOT NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

## member_phone_numbers

```text
id UUID PK
member_id UUID FK members NOT NULL
phone_number VARCHAR(30) NOT NULL
provider VARCHAR(50) NULL
is_primary BOOLEAN DEFAULT FALSE
is_active BOOLEAN DEFAULT TRUE
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Rules: multiple phones allowed; one active phone cannot belong to multiple members; primary phone is unique per member where applicable.

## temporary_contributors

```text
id UUID PK
phone_number VARCHAR(30) NOT NULL
provider VARCHAR(50) NULL
provider_name VARCHAR(200) NULL
provider_account_reference VARCHAR(200) NULL
first_name VARCHAR(100) NULL
last_name VARCHAR(100) NULL
status VARCHAR(30) NOT NULL
converted_member_id UUID FK members NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Statuses: `UNVERIFIED`, `UNDER_REVIEW`, `LINKED`, `CONVERTED`.

## member_merge_requests

```text
id UUID PK
source_member_id UUID FK members NOT NULL
target_member_id UUID FK members NOT NULL
requested_by UUID FK users NOT NULL
reason TEXT NOT NULL
status VARCHAR(30) NOT NULL
reviewed_by UUID FK users NULL
reviewed_at TIMESTAMPTZ NULL
review_remarks TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

Only the System Administrator may approve/reject a merge.

---

# 8. CONTRIBUTIONS / PAYMENTS

## contribution_types

```text
id UUID PK
name VARCHAR(100) UNIQUE NOT NULL
description TEXT NULL
is_active BOOLEAN DEFAULT TRUE
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Seed:

```text
Tithe
Thanksgiving
Higher Life Partners
Building Project
Other
```

## payment_transactions

```text
id UUID PK
provider VARCHAR(50) NOT NULL
provider_reference VARCHAR(200) NOT NULL
transaction_phone VARCHAR(30) NOT NULL
provider_name VARCHAR(200) NULL
amount NUMERIC(12,2) NOT NULL
currency CHAR(3) NOT NULL
status VARCHAR(30) NOT NULL
failure_code VARCHAR(100) NULL
failure_reason TEXT NULL
provider_data JSONB NULL
confirmed_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Statuses:

`PENDING`, `SUCCESSFUL`, `FAILED`, `CANCELLED`, `DUPLICATE`.

## contributions

```text
id UUID PK
contribution_number VARCHAR(50) UNIQUE NOT NULL
member_id UUID FK members NULL
temporary_contributor_id UUID FK temporary_contributors NULL
payment_transaction_id UUID FK payment_transactions NULL
contribution_type_id UUID FK contribution_types NOT NULL
custom_type_description TEXT NULL
amount NUMERIC(12,2) NOT NULL
currency CHAR(3) NOT NULL
payment_mode VARCHAR(40) NOT NULL
entry_method VARCHAR(20) NOT NULL
reference_number VARCHAR(200) NULL
status VARCHAR(30) NOT NULL
recorded_by UUID FK users NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Payment modes:

`CASH`, `MOBILE_MONEY`, `CHEQUE`, `BANK_TRANSACTION`, `OTHER`.

Entry methods:

`AUTOMATIC`, `MANUAL`.

A contribution must reference exactly one contributor identity: member OR temporary contributor.

Automatic contributions require a payment transaction.

Manual contributions may not have one.

---

# 9. PAYMENT EXCEPTION LOGIC

Failed payment:
- no successful contribution
- no receipt
- no thank-you
- preserve provider reason/code

Cancelled:
- no successful contribution
- no receipt
- no thank-you

Delayed confirmation:
- remain pending
- no receipt/thank-you until confirmed

Duplicate rule:

```text
Provider Reference Number + Contribution Type
```

Exact duplicate: no second contribution/receipt; log and notify as appropriate.

Same provider reference with different contribution type: flag for review.

Merchant-number transaction: do not automatically generate contributor receipt; log and notify an authorized user.

---

# 10. RECEIPTS

## receipts

```text
id UUID PK
receipt_number VARCHAR(20) UNIQUE NOT NULL
contribution_id UUID UNIQUE FK contributions NOT NULL
generated_by_user_id UUID FK users NULL
generated_by_system BOOLEAN NOT NULL
pdf_storage_key TEXT NOT NULL
status VARCHAR(20) NOT NULL
generated_at TIMESTAMPTZ NOT NULL
deleted_at TIMESTAMPTZ NULL
deleted_by UUID FK users NULL
created_at TIMESTAMPTZ NOT NULL
```

Format:

```text
HLC-XXXXXXX
```

Exactly seven digits after `HLC-`.

Receipt content:
- TCI Higher Life Center
- logo
- receipt number
- contributor name
- amount
- contribution type
- payment mode
- date
- relevant reference where appropriate
- Generated By

Phone number must not appear.

Automatic receipt = `generated_by_system = TRUE` and immutable.

Manual receipt = creator recorded in `generated_by_user_id`, editable only by creator if permitted.

## receipt_edits

```text
id UUID PK
receipt_id UUID FK receipts NOT NULL
user_id UUID FK users NOT NULL
field_name VARCHAR(100) NOT NULL
old_value TEXT NULL
new_value TEXT NULL
reason TEXT NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

## receipt_delivery_records

```text
id UUID PK
receipt_id UUID FK receipts NOT NULL
recipient_phone VARCHAR(30) NOT NULL
channel VARCHAR(20) NOT NULL
status VARCHAR(30) NOT NULL
secure_link TEXT NULL
provider_reference VARCHAR(200) NULL
attempt_count INTEGER DEFAULT 0
last_attempt_at TIMESTAMPTZ NULL
delivered_at TIMESTAMPTZ NULL
failure_reason TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

V1 channel is SMS. Automatic receipt SMS contains text receipt information plus secure PDF link, not a PDF attachment.

---

# 11. REQUISITIONS

## requisitions

```text
id UUID PK
requisition_number VARCHAR(50) UNIQUE NOT NULL
requester_name VARCHAR(200) NOT NULL
requester_phone VARCHAR(30) NOT NULL
requester_member_id UUID FK members NULL
ministry VARCHAR(150) NULL
team VARCHAR(150) NULL
department VARCHAR(150) NULL
group_name VARCHAR(150) NULL
purpose VARCHAR(255) NOT NULL
description TEXT NOT NULL
amount_requested NUMERIC(12,2) NOT NULL
date_funds_needed DATE NULL
preferred_disbursement_method VARCHAR(30) NULL
preferred_disbursement_details TEXT NULL
remarks TEXT NULL
status VARCHAR(40) NOT NULL
related_requisition_id UUID FK requisitions NULL
submitted_at TIMESTAMPTZ NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Statuses:

`DRAFT`, `SUBMITTED`, `PENDING_REVIEW`, `UNDER_REVIEW`, `APPROVED`, `REJECTED`, `PENDING_DISBURSEMENT`, `DISBURSED`, `EVIDENCE_SUBMITTED`, `UNDER_VERIFICATION`, `COMPLETED`.

Rejected requisitions cannot be edited/resubmitted. A new requisition receives a new number and may reference the old one.

## requisition_attachments

Initial supporting documents must be separate from post-disbursement evidence.

```text
id UUID PK
requisition_id UUID FK requisitions NOT NULL
uploaded_by_user_id UUID FK users NULL
uploader_name VARCHAR(200) NOT NULL
uploader_phone VARCHAR(30) NULL
file_name VARCHAR(255) NOT NULL
file_type VARCHAR(100) NOT NULL
storage_key TEXT NOT NULL
file_size BIGINT NOT NULL
created_at TIMESTAMPTZ NOT NULL
```

## requisition_assignments

```text
id UUID PK
requisition_id UUID FK requisitions NOT NULL
assigned_to UUID FK users NOT NULL
assigned_by UUID FK users NULL
assigned_at TIMESTAMPTZ NOT NULL
released_at TIMESTAMPTZ NULL
release_reason TEXT NULL
is_current BOOLEAN DEFAULT TRUE
```

Start Working automatically assigns the authorized user and records the timestamp. Normal concurrent processing is locked. Authorized users can reassign/unlock with reason and history.

## requisition_approvals

```text
id UUID PK
requisition_id UUID FK requisitions NOT NULL
approver_id UUID FK users NOT NULL
decision VARCHAR(20) NOT NULL
approved_amount NUMERIC(12,2) NULL
reason TEXT NULL
remarks TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

Approval decision: `APPROVED` or `REJECTED`.

Rejected requires a reason. Approval does not disburse funds.

## requisition_disbursements

```text
id UUID PK
requisition_id UUID FK requisitions NOT NULL
amount NUMERIC(12,2) NOT NULL
method VARCHAR(30) NOT NULL
recipient_name VARCHAR(200) NOT NULL
recipient_phone VARCHAR(30) NULL
transaction_reference VARCHAR(200) NULL
processed_by UUID FK users NOT NULL
disbursed_at TIMESTAMPTZ NOT NULL
remarks TEXT NULL
```

Methods: `CASH`, `MOBILE_MONEY`, `BANK_TRANSFER`, `OTHER`.

## requisition_evidence

```text
id UUID PK
requisition_id UUID FK requisitions NOT NULL
uploaded_by_user_id UUID FK users NULL
uploader_name VARCHAR(200) NOT NULL
uploader_phone VARCHAR(30) NULL
file_name VARCHAR(255) NOT NULL
file_type VARCHAR(100) NOT NULL
storage_key TEXT NOT NULL
file_size BIGINT NOT NULL
status VARCHAR(30) NOT NULL
reviewed_by UUID FK users NULL
reviewed_at TIMESTAMPTZ NULL
review_remarks TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

Statuses: `UPLOADED`, `UNDER_REVIEW`, `VERIFIED`, `REJECTED`.

Multiple evidence files per requisition are allowed.

A rejected evidence file remains historical and the requester may upload corrected/additional evidence.

Secure upload links identify the requisition automatically; requester does not manually enter the ID.

Closure requires:

```text
APPROVED
→ DISBURSED
→ EVIDENCE SUBMITTED
→ EVIDENCE VERIFIED
→ COMPLETED
```

---

# 12. NOTIFICATIONS

## notification_templates

```text
id UUID PK
name VARCHAR(150) UNIQUE NOT NULL
notification_type VARCHAR(50) NOT NULL
contribution_type_id UUID FK contribution_types NULL
template_body TEXT NOT NULL
is_active BOOLEAN DEFAULT TRUE
created_by UUID FK users NULL
updated_by UUID FK users NULL
created_at TIMESTAMPTZ NOT NULL
updated_at TIMESTAMPTZ NOT NULL
```

Templates must be configurable and support contribution-specific thank-you messages.

## notifications

```text
id UUID PK
notification_type VARCHAR(50) NOT NULL
recipient_name VARCHAR(200) NULL
recipient_phone VARCHAR(30) NOT NULL
related_record_type VARCHAR(100) NOT NULL
related_record_id UUID NOT NULL
channel VARCHAR(20) NOT NULL
template_id UUID FK notification_templates NULL
message TEXT NOT NULL
status VARCHAR(30) NOT NULL
created_at TIMESTAMPTZ NOT NULL
sent_at TIMESTAMPTZ NULL
delivered_at TIMESTAMPTZ NULL
```

V1 = SMS.

Types include:

`THANK_YOU`, `RECEIPT`, `PAYMENT_FAILURE`, `REQUISITION_APPROVED`, `REQUISITION_REJECTED`, `READY_FOR_DISBURSEMENT`, `PROOF_UPLOAD`, `EVIDENCE_REJECTED`, `RECEIPT_GENERATION_FAILED`, `THANK_YOU_FAILED`, `MERCHANT_TRANSACTION`, `DUPLICATE_TRANSACTION`, `OTHER`.

Store the rendered message, not only the template reference.

## notification_attempts

```text
id UUID PK
notification_id UUID FK notifications NOT NULL
attempt_number INTEGER NOT NULL
provider_reference VARCHAR(200) NULL
provider_response JSONB NULL
status VARCHAR(30) NOT NULL
failure_code VARCHAR(100) NULL
failure_reason TEXT NULL
attempted_at TIMESTAMPTZ NOT NULL
```

Thank-you messages: maximum 3 attempts.

Receipt generation: maximum 3 attempts.

After configured maximum failures, create Manual Action and notify an authorized user.

Notification failure never reverses the underlying event.

---

# 13. MANUAL ACTIONS

## manual_actions

```text
id UUID PK
action_type VARCHAR(100) NOT NULL
related_record_type VARCHAR(100) NOT NULL
related_record_id UUID NOT NULL
description TEXT NOT NULL
attempt_count INTEGER DEFAULT 0
priority VARCHAR(20) NOT NULL
status VARCHAR(30) NOT NULL
assigned_to UUID FK users NULL
created_at TIMESTAMPTZ NOT NULL
resolved_at TIMESTAMPTZ NULL
resolved_by UUID FK users NULL
resolution_notes TEXT NULL
```

Examples:

`RECEIPT_GENERATION_FAILED`, `THANK_YOU_NOTIFICATION_FAILED`, `MERCHANT_TRANSACTION`, `DUPLICATE_TRANSACTION_REVIEW`, `UNKNOWN_CONTRIBUTOR_REVIEW`, `OTHER`.

Statuses: `OPEN`, `ASSIGNED`, `IN_PROGRESS`, `RESOLVED`.

Priorities: `LOW`, `MEDIUM`, `HIGH`, `CRITICAL`.

---

# 14. AUDIT

## audit_logs

```text
id UUID PK
user_id UUID FK users NULL
actor_type VARCHAR(20) NOT NULL
action VARCHAR(100) NOT NULL
entity_type VARCHAR(100) NOT NULL
entity_id UUID NOT NULL
old_values JSONB NULL
new_values JSONB NULL
reason TEXT NULL
result VARCHAR(30) NOT NULL
provider_reference VARCHAR(200) NULL
ip_address INET NULL
user_agent TEXT NULL
created_at TIMESTAMPTZ NOT NULL
```

Actor types:

`USER`, `SYSTEM`, `PROVIDER`.

Audit:

- user creation/deactivation
- permission changes
- password/admin recovery
- member changes
- phone changes
- merges/linking
- contribution/payment events
- receipt creation/edit/deletion
- notifications
- requisition submission/assignment/reassignment/approval/rejection/disbursement
- evidence events
- manual actions

Audit records are append-only during normal operation and must not be editable/deletable through normal application operations.

---

# 15. DATABASE CONSTRAINTS / INDEXES

Use database constraints wherever possible.

Required constraints include:

- unique receipt number
- unique contribution number
- unique requisition number
- unique contribution-to-receipt relationship
- active phone uniqueness across members
- one active primary phone per member
- valid contributor identity: member XOR temporary contributor
- automatic contribution requires payment transaction
- approved amount requires approval
- disbursement requires requisition
- no normal transition from REJECTED back to editable/submitted
- audit immutability through application architecture

Index high-value fields including:

```text
users.email
members.member_number
members.email
member_phone_numbers.phone_number
payment_transactions.provider_reference
payment_transactions.transaction_phone
payment_transactions.status
contributions.contribution_number
contributions.member_id
contributions.created_at
receipts.receipt_number
requisitions.requisition_number
requisitions.status
requisitions.requester_phone
notifications.status
notifications.recipient_phone
audit_logs.entity_type/entity_id
audit_logs.created_at
```

Cross-table rules must also be enforced in services and transactional workflows.

---

# 16. ID GENERATION

Use UUIDs as database primary keys.

Human-readable business IDs are separate.

Examples:

```text
MEM-000012
CON-00001245
HLC-5831047
REQ-000125
```

Never generate business IDs by counting rows.

Generation must be concurrency-safe.

Receipt number collisions must be handled safely and receipt numbers must never be reused.

---

# 17. CORE WORKFLOWS

## Automatic contribution

```text
Provider/USSD
→ contribution type
→ amount
→ payment
→ provider confirmation
→ transaction phone matching
→ member OR temporary contributor
→ contribution
→ immutable receipt
→ personalized thank-you
→ receipt SMS + secure PDF link
```

## Manual contribution

```text
System User
→ find/create member
→ contribution details
→ save
→ manual receipt
→ thank-you SMS
→ PDF download
```

## Requisition

```text
Requester
→ draft
→ submit
→ pending review
→ Start Working
→ automatic assignment
→ review
→ approve/reject
```

Approved:

```text
Approved
→ Pending Disbursement
→ Disbursed
→ secure evidence upload
→ evidence verification
→ Completed
```

Rejected:

```text
Rejected
→ historical record
→ new requisition required
```

---

# 18. RECEIPT SERVICE

Implement:

```python
ReceiptService.generate_for_contribution(contribution)
```

Responsibilities:

1. Verify eligibility.
2. Prevent duplicate receipt.
3. Generate unique HLC number.
4. Render PDF.
5. Store PDF through storage abstraction.
6. Create receipt record.
7. Audit the event.
8. Queue required notifications.

Receipt generation must be idempotent.

After three failed receipt-generation attempts:

- preserve successful contribution
- create Manual Action
- notify authorized user

---

# 19. PROVIDER ABSTRACTIONS

Do not permanently couple the domain to MTN MoMo, Paystack, a specific SMS provider, or a specific storage vendor.

Create abstractions such as:

```python
PaymentProvider
SmsProvider
StorageService
```

The first implementation may use mocks/fakes for development.

Provider callbacks must be safe to replay.

---

# 20. BACKGROUND PROCESSING

Use Celery + Redis for asynchronous operations such as:

```text
process_payment_confirmation
generate_receipt
send_notification
retry_notification
send_requisition_notification
create_manual_action
cleanup_expired_secure_links
```

Tasks must be idempotent.

Never assume a Celery task executes exactly once.

---

# 21. SECURITY

Implement:

- CSRF protection
- secure password hashing
- session expiry
- failed-login protection
- server-side permission enforcement
- secure signed/expiring links
- file type and size validation
- safe file handling
- storage isolation
- input validation
- secure headers
- HTTPS in production
- secret management through environment/deployment configuration
- immutable audit architecture

Never commit secrets.

---

# 22. REPORTING

Support:

### Contribution report
Contribution ID, receipt number, contributor, type, amount, payment mode, entry method, date, reference, status, generated by.

### Receipt report
Receipt number, contribution ID, contributor, amount, type, payment mode, date, entry method, generated by, status.

### Requisition report
Requisition ID, requester, ministry/team/department/group, purpose, requested/approved/disbursed amounts, preferred/actual methods, assigned user, status, dates, evidence status.

### Member report
Member ID, name, email, phones, primary phone, ministry/team/department, status, dates.

### User activity
Login, contribution, receipt, member, requisition, permission and other auditable activity.

Filters:

- date range
- member/contributor
- contribution type
- payment mode
- entry method
- user
- requisition status
- ministry/team/department/group
- requester
- disbursement method
- evidence status
- notification status

Periods:

- today
- yesterday
- week
- month
- year
- custom

Exports:

- PDF
- Excel
- CSV

Always enforce permissions on exports.

---

# 23. TESTING

Write automated tests for:

### Authentication
- login success/failure
- inactive user
- password reset
- expired reset
- single-use reset

### Authorization
- allowed/denied permissions
- configurable roles
- System User cannot create System Administrator

### Members
- member creation
- multiple phones
- phone conflict
- deactivation
- temporary contributor
- merge approval

### Contributions
- manual contribution
- automatic contribution
- failed/cancelled/delayed payment
- duplicate
- merchant transaction
- unknown contributor

### Receipts
- unique HLC number
- seven-digit format
- duplicate prevention
- automatic immutability
- manual editing
- edit audit
- soft deletion
- PDF generation

### Requisitions
- draft
- submit
- assignment lock
- reassignment
- approval
- rejection
- disbursement
- multiple evidence
- evidence rejection/re-upload
- verification
- closure

### Notifications
- successful send
- failure
- retry
- maximum attempts
- Manual Action creation

### Audit
- critical actions create audit events
- normal application code cannot modify/delete audit events

---

# 24. DEVELOPMENT ORDER

## Phase 1 — Infrastructure

1. Repository
2. Docker
3. PostgreSQL
4. Redis
5. Django project
6. Environment configuration
7. settings
8. Celery
9. logging
10. tests
11. CI foundation

## Phase 2 — Core database

1. base UUID/timestamp models
2. accounts
3. members
4. contributions
5. receipts
6. requisitions
7. notifications
8. manual actions
9. audit
10. migrations
11. constraints
12. indexes
13. seed data

## Phase 3 — Security

Authentication, password reset, roles, permissions, sessions, administrator recovery, audit.

## Phase 4 — Contribution core

Members, phone matching, temporary contributors, manual contributions, automatic processing, duplicate detection, receipts.

## Phase 5 — Notifications

Templates, SMS abstraction, delivery, retry, Manual Actions.

## Phase 6 — Requisitions

Requester portal, drafts, submission, assignment, approval/rejection, disbursement, supporting documents, evidence, verification, closure.

## Phase 7 — Reports

Dashboards, search, filtering, exports, audit views.

## Phase 8 — External integrations

Payment provider, SMS provider, production object storage and deployment.

---

# 25. FIRST IMPLEMENTATION MILESTONE

The first coding milestone is **Core Infrastructure + PostgreSQL**.

Deliver:

```text
Docker Compose
PostgreSQL
Redis
Django project
Environment configuration
Core app
Accounts app
Members app
Contributions app
Receipts app
Requisitions app
Notifications app
Audit app
Reports app
Models
Migrations
Constraints
Indexes
Seed mechanism
Tests
Health check
README
```

The milestone is complete when:

1. PostgreSQL starts.
2. Django connects.
3. Migrations succeed.
4. Core tables exist.
5. Seed data loads idempotently.
6. Tests execute.
7. Celery connects to Redis.
8. Application starts.
9. Health check verifies application/database readiness.
10. No secrets are committed.

---

# 25.1 SECOND IMPLEMENTATION MILESTONE — MILESTONE 2

**Milestone:** M2 — Authentication, Authorization & Core Application UI  
**Status:** Completed — M2 consistency fixes and role-permission management added; local runtime verification pending  
**Depends on:** M1 — Core Infrastructure & PostgreSQL Data Model  
**Objective:** Build and validate the complete authentication, authorization, system administration, and initial browser-based application interface using the real Django backend and PostgreSQL database.

## Scope
- **Included:**
  - System Administrator authentication
  - System User authentication
  - Session management & Logout
  - Password reset with secure, single-use, expiring tokens
  - System Administrator multi-factor recovery (Email OTP → Phone OTP)
  - Role and permission enforcement (URL, View, Service, Template layers)
  - System User management (List, search, filter, view details, create user, deactivate user)
  - Role management (System Administrator creates custom roles and assigns/removes permissions under each role)
  - Permission assignment (System Users receive access through an administrator-created role)
  - Core application layout (Responsive sidebar, top navigation, user profile menu, notification area, flash messages)
  - Dashboard (Welcome section, summary cards, recent activity, pending manual actions, module placeholders)
  - Permission-aware UI
  - Authentication and administration audit logging
  - Frontend-to-backend integration (Django Templates, HTML, CSS, JavaScript, HTMX)
  - Comprehensive browser-based and automated testing (M2-BT-001 through M2-BT-012)
- **Excluded:**
  - Member management (Milestone 3)
  - Contribution recording & payment provider integration
  - Receipt generation & SMS provider integration
  - Requisition & disbursement processing
  - Production deployment

## Acceptance Criteria & Test Scenarios
- `M2-BT-001`: System Administrator login reaches dashboard.
- `M2-BT-002`: Invalid login fails without revealing account existence.
- `M2-BT-003`: System User login reaches dashboard.
- `M2-BT-004`: Inactive user login is rejected.
- `M2-BT-005`: Authorized creation of System User.
- `M2-BT-006`: Role and permission assignment updates user capabilities.
- `M2-BT-007`: Unauthorized action blocked by backend security layer.
- `M2-BT-008`: User deactivation prevents authentication while preserving history.
- `M2-BT-009`: Valid password reset token permits exactly one reset.
- `M2-BT-010`: Expired password reset token is rejected.
- `M2-BT-011`: System Administrator recovery via dynamic Email OTP + Phone OTP.
- `M2-BT-012`: All security and administrative actions produce immutable audit records.
- `M2-BT-013`: System User cannot create or assign the System Administrator role through the service layer.
- `M2-BT-014`: User management authorization is enforced at the service layer.
- `M2-BT-015`: System Administrator can create and configure custom roles with selected permissions.
- `M2-BT-016`: System User cannot access role management.
- `M2-BT-019`: System User creation exposes only System Administrator-created custom roles.
- `M2-BT-020`: Custom role pages display and configure the permissions assigned to that role.
- `M2-BT-017`: Repeated failed login attempts are throttled.
- `M2-BT-018`: External post-login redirects are rejected.
- Current M2 regression target: 51 automated tests (including M1 & M2 test suites).

---

# 25.2 THIRD IMPLEMENTATION MILESTONE — MILESTONE 3

**Milestone:** M3 — Member Management & Contributor Identification Foundation  
**Status:** Completed — Full service layer, responsive UI templates, and automated test suite implemented; 65 total tests passing across M1, M2, and M3  
**Depends on:** M1 — Core Infrastructure & Data Model, M2 — Authentication & Authorization  
**Objective:** Build and validate the complete Member Management module, establishing verified member profiles, strict phone-based contributor identification, temporary contributor review/conversion/linking workflows, and System Administrator-controlled member merges.

## Scope
- **Included:**
  - System-generated member ID (`MEM-000001` via `IdGenerator.generate_member_number()`)
  - Member profile management (name, optional email, ministry, team, department, status)
  - Multi-phone number management with strict active uniqueness across members and single active primary enforcement
  - Inactive phone availability: releasing inactive phone numbers for reassignment while preserving historical records
  - Strict contributor identification: transaction phone number is the sole identifier; name or account inference is strictly prohibited
  - Temporary contributor queue: unverified incoming mobile money contributors automatically queued
  - Temporary contributor conversion to new church member (preserving contributions and primary phone)
  - Temporary contributor linking to existing church member (reassigning contributions and phone)
  - Member merge request initiation by authorized staff
  - Strict System Administrator merge approval boundary: atomic contribution reassignment, non-conflicting phone transfer, and source deactivation
  - Full audit logging for member creation, edits, deactivations, phone assignments, reviews, and merges
  - Member management UI:
    - Member directory with search across member number, name, phone, email, and ministry/status filters
    - Member profile view with phone management, giving history foundation, and merge actions
    - Member registration and edit forms with validation styling
    - Temporary contributor review dashboard with inline link and convert modals
    - Member merge request management queue
- **Excluded:**
  - Automated payment webhook endpoints & external gateway polling (Milestone 4)
  - Receipt generation & automated SMS/Email dispatch (Milestone 5)
  - Requisitions & financial disbursements (Milestone 6)

## Acceptance Criteria & Test Scenarios
- `M3-BT-001`: Member creation generates system ID (`MEM-000001`) and assigns initial active primary phone.
- `M3-BT-002`: Member supports multiple phone numbers with exactly one active primary phone.
- `M3-BT-003`: Active phone conflict across members is rejected.
- `M3-BT-004`: Inactive phone numbers can be reassigned to another member.
- `M3-BT-005`: Member search correctly queries member number, name, phone, email, and status.
- `M3-BT-006`: Contributor identification strictly matches on transaction phone number; provider name differences are disregarded.
- `M3-BT-007`: Unrecognized transaction phone creates an unverified Temporary Contributor record.
- `M3-BT-008`: Converting Temporary Contributor to member creates profile and reassigns contributions.
- `M3-BT-009`: Linking Temporary Contributor to existing member reassigns contributions and updates phone list.
- `M3-BT-010`: Member merge request requires reason and cannot merge a member to self.
- `M3-BT-011`: System Administrator merge approval atomically reassigns contributions and phones, and deactivates source.
- `M3-BT-012`: Non-administrator cannot approve or reject member merge requests.
- `M3-BT-013`: Member deactivation preserves historical contributions, receipts, and audit trail.
- `M3-BT-014`: Member management UI enforces role-based permission boundaries.
- All Milestone 1 and Milestone 2 tests continue passing.
- Current project regression target: 65 automated tests.

---

# 25.3 FOURTH IMPLEMENTATION MILESTONE — MILESTONE 4

**Milestone:** M4 — Contribution Management  
**Status:** Completed — Full service layer, UI workflows, duplicate protection rules, and automated test suite implemented; 71 total tests passing across M1, M2, M3, and M4  
**Depends on:** M1 — Core Infrastructure & Data Model, M2 — Authentication & Authorization, M3 — Member Management & Contributor Identification  
**Objective:** Build and validate the complete Church Contribution Management module, enabling authorized System Users to record, view, search, validate, and manage manual church contributions, establish duplicate protection foundation, integrate M3 member/phone resolution, and enforce append-only financial audit trails.

## Scope
- **Included:**
  - System-generated contribution ID (`CON-00000001` via `IdGenerator.generate_contribution_number()`)
  - Supported contribution types: Tithe, Thanksgiving, Higher Life Partners, Building Project, Other (with mandatory custom description for Other)
  - Supported payment modes: Cash, Mobile Money, Cheque, Bank Transaction (explicitly displayed as "Bank Transaction"), Other
  - Entry methods: Manual and Automatic
  - Manual contribution recording for active church members with permission enforcement (`contribution.create`)
  - Contributor association: Member / Contributor association with dynamic AJAX search, active member validation, and deactivated member blocking
  - Financial record integrity: querysets and model deletion overrides prevent physical deletion of financial records
  - Multi-criteria search and filtering: Contribution ID, Member name/number, Temporary contributor phone/name, Reference number, Provider reference, Receipt number, Contribution type, Payment mode, Entry method, Status, Date ranges
  - Contribution detail view with contributor history, payment mode info, receipt cross-reference status, and full audit logs
  - Automatic contribution foundation: `PaymentTransaction` model and status lifecycle (`PENDING`, `CONFIRMED`, `FAILED`, `DUPLICATE`)
  - Duplicate-payment protection: Provider Reference Number + Contribution Type rule (exact duplicate blocked with `DUPLICATE` status; same provider reference with different type flagged for review with `PARTIAL` audit result)
  - Strict M3 phone-based contributor resolution for automatic contributions
  - Immutable audit logging for all contribution actions (`CONTRIBUTION_CREATED`, `AUTOMATIC_CONTRIBUTION_RECORDED`, `DUPLICATE_CONTRIBUTION_BLOCKED`, `CONTRIBUTION_FLAGGED_FOR_REVIEW`)
  - Contribution UI:
    - Contribution directory with KPI counters (Total Contributions, Confirmed Amount, Manual Count, Automatic Count)
    - Manual contribution entry form with live member search, type description conditional display, and review card
    - Contribution detail and audit inspection page
    - Member AJAX search API endpoint (`/contributions/members/search/`)
- **Excluded:**
  - Live external payment-provider webhook integrations & polling daemons (Milestone 6)
  - Receipt generation, PDF export, & automated notification dispatch (Milestone 5)
  - Requisitions & financial disbursements (Milestone 7)

## Acceptance Criteria & Test Scenarios
- `M4-BT-001`: Manual contribution creation generates unique system ID (`CON-00000001`) and sets status to Confirmed.
- `M4-BT-002`: All five contribution types are supported; "Other" requires a non-empty description.
- `M4-BT-003`: All payment modes are supported and interface displays "Bank Transaction".
- `M4-BT-004`: Contribution amount must be positive; non-numeric or zero/negative amounts are rejected.
- `M4-BT-005`: Manual contributions require an active member; contributions for deactivated members are rejected.
- `M4-BT-006`: Contribution creation enforces `contribution.create` permission.
- `M4-BT-007`: Contribution viewing and detail pages enforce `contribution.view` permission.
- `M4-BT-008`: Financial records cannot be physically deleted (bulk and instance delete are rejected).
- `M4-BT-009`: Contribution creation and actions emit immutable audit log entries.
- `M4-BT-010`: Contribution search filters by ID, member, phone, reference, type, mode, entry method, and dates.
- `M4-BT-011`: Automatic contributions derive contributor strictly from transaction phone using M3 rules.
- `M4-BT-012`: Duplicate transaction with identical provider reference and contribution type is blocked.
- `M4-BT-013`: Duplicate transaction with identical provider reference but different type is flagged for review.
- `M4-BT-014`: UI contribution creation workflow provides member search, validation, and redirect.
- `M4-BT-015`: AJAX member search API endpoint returns matching active members.
- All Milestone 1, 2, and 3 tests continue passing without regression.
- Current project regression target: 71 automated tests.

---

# 25.4 FIFTH IMPLEMENTATION MILESTONE — MILESTONE 5

**Milestone:** M5 — Receipt Management  
**Status:** Completed — Full domain service layer, production WeasyPrint PDF generation and storage service, creator-only manual receipt modification with field-level audit trail, soft-deletion lifecycle, privacy-preserving public verification portal, cryptographically signed secure token access, and automated test suite implemented; 87 tests passing across M1, M2, M3, M4, and M5 (96 total test suite passes across all modules).  
**Depends on:** M1 — Core Infrastructure & Data Model, M2 — Authentication & Authorization, M3 — Member Management & Contributor Identification, M4 — Contribution Management  
**Objective:** Build and validate the complete Church Receipt Management module, enabling authorized System Users to generate, view, edit (creator-only), and soft-delete manual receipts; automatically issuing immutable receipts and SMS dispatches with tamper-proof download links for automatic contributions; enforcing contributor phone number privacy across all documents and verification channels; and establishing an append-only audit trail and public receipt verification.

## Scope
- **Included:**
  - System-generated unique receipt number: `HLC-XXXXXXX` (strictly 7 digits after `HLC-`, collision-safe)
  - One-to-one relationship between Contribution and Receipt; idempotent generation
  - Automatic receipt properties: `generated_by_system = True`, `generated_by_display = "HLC/System"`, strictly immutable and cannot be deleted
  - Manual receipt properties: `generated_by_user` recorded, creator-only manual editing enforced in service layer
  - Field-level audit trail for manual edits via `ReceiptEdit` (`receipt_id`, `user_id`, `field_name`, `old_value`, `new_value`, `reason`, `created_at`) with mandatory non-empty reason
  - Soft-deletion lifecycle: `soft_delete()` sets `status = DELETED`, records `deleted_at` and `deleted_by`, prevents physical deletion on instance and queryset levels
  - Queryset visibility: default `objects` manager excludes soft-deleted receipts; `all_objects` retains full history for System Administrator review
  - Contributor Phone Number Privacy Invariant: contributor phone numbers must NEVER appear in receipt HTML, PDF, or verification output
  - PDF Generation and Storage: WeasyPrint HTML-to-PDF rendering conforming to official church branding and persisted in `StorageService` (`receipts/YYYY/MM/HLC-XXXXXXX.pdf`)
  - Public Receipt Verification Portal (`/receipts/verify/<receipt_number>/`): validates active, soft-deleted (revoked), and non-existent receipts; displays confirmation without exposing phone numbers
  - Cryptographically signed secure access tokens (`TimestampSigner`) for external SMS receipt downloads (`/receipts/access/<token>/`)
  - Automatic SMS dispatch foundation (`ReceiptDeliveryRecord` and `Notification`) with graceful handling of missing or unverified phone numbers
  - Receipt UI:
    - Receipt directory (`/receipts/`) with KPI metrics, multi-criteria filtering (number, contributor, date, mode, type, source), and admin soft-deleted toggle
    - Receipt detail view (`/receipts/<receipt_id>/`) with status badge, contributor card, financial ledger, edit history log, and SMS delivery tracking
    - Manual receipt edit view (`/receipts/<receipt_id>/edit/`) with creator-only guard and mandatory reason
    - PDF download and streaming view (`/receipts/<receipt_id>/pdf/`)
    - Public verification view (`/receipts/verify/`)
    - Cross-linkage in Contribution Detail (`/contributions/<contribution_id>/`)
- **Excluded:**
  - Live external payment-provider webhook integrations & polling daemons (Milestone 6)
  - Requisitions & financial disbursements (Milestone 7)
  - Financial reports and analytics exports (Milestone 8)

## Acceptance Criteria & Test Scenarios
- `M5-BT-001`: Receipt generation creates unique `HLC-XXXXXXX` (strictly 7 digits).
- `M5-BT-002`: A contribution has at most one receipt; repeated generation returns existing record (idempotency).
- `M5-BT-003`: Automatic contributions generate system receipts with `generated_by_system=True` and `"HLC/System"` display.
- `M5-BT-004`: Automatic receipts are strictly immutable; attempts to modify fail validation.
- `M5-BT-005`: Automatic receipts cannot be deleted by anyone, including the System Administrator.
- `M5-BT-006`: Manual receipt edits are creator-only. Other users cannot edit even if permitted.
- `M5-BT-007`: Manual edits require mandatory non-empty reason and record field-level `ReceiptEdit` entries and audit logs.
- `M5-BT-008`: Manual receipts can be soft-deleted by their creator with `receipt.delete_own`.
- `M5-BT-009`: Physical deletion of receipts is strictly prohibited on instance and queryset levels.
- `M5-BT-010`: Active manager excludes soft-deleted receipts; `all_objects` includes them for administrator review.
- `M5-BT-011`: Contributor phone numbers must NEVER appear in receipt HTML, PDF, or verification output.
- `M5-BT-012`: PDF is generated and persisted in StorageService with valid `%PDF` header.
- `M5-BT-013`: Public receipt verification validates active, soft-deleted, and non-existent receipts.
- `M5-BT-014`: Secure token allows tamper-proof access for external SMS downloads.
- `M5-BT-015`: Automatic receipt dispatches SMS notification; missing phone is handled gracefully without failing.
- `M5-BT-016`: UI routes for list, detail, pdf export, edit, and public verify enforce permissions.
- All Milestone 1, 2, 3, and 4 tests continue passing without regression.
- Current project regression target: 87 automated tests (M1-M5), with 96 total automated test suite passing.

---

# 26. RECOMMENDED PROJECT STRUCTURE

```text
tci-hlc-finance/
├── apps/
│   ├── accounts/
│   ├── audit/
│   ├── contributions/
│   ├── core/
│   ├── members/
│   ├── notifications/
│   ├── receipts/
│   ├── reports/
│   └── requisitions/
├── config/
│   ├── settings/
│   │   ├── base.py
│   │   ├── development.py
│   │   └── production.py
│   ├── asgi.py
│   ├── celery.py
│   ├── urls.py
│   └── wsgi.py
├── templates/
├── static/
├── tests/
├── scripts/
├── docker/
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── manage.py
├── pyproject.toml
└── README.md
```

---

# 27. CODING AI INSTRUCTIONS

Work incrementally.

For every task:

1. Explain the intended change briefly.
2. Identify affected files.
3. Implement the smallest complete change.
4. Run relevant tests.
5. Run migrations/checks where relevant.
6. Report failures honestly.
7. Never silently change requirements.
8. Never invent new business rules without documenting the decision.
9. Do not build frontend screens before the relevant backend/domain foundation exists.
10. Keep financial operations transactional.
11. Keep external integrations behind interfaces.
12. Prefer maintainable, explicit code over shortcuts.

If a requirement is genuinely ambiguous, stop at the architectural decision point, identify the ambiguity, and request/record the decision instead of silently choosing a behavior that could affect financial integrity.

---

# 28. V1 OUT OF SCOPE

Do not make these mandatory:

- WhatsApp
- contributor/member accounts
- permanent specific payment provider
- permanent specific SMS provider
- undefined advanced approval thresholds
- undefined approval hierarchies

The architecture must remain extensible for these future capabilities.

---

# 29. FINAL ACCEPTANCE CRITERIA

Version 1 must be capable of:

- secure System User authentication
- configurable permission enforcement
- exactly one System Administrator
- member management and multiple phones
- strict phone-based automatic contributor identification
- temporary contributors
- manual and automatic contributions
- payment exception handling
- duplicate detection
- unique HLC receipts
- immutable automatic receipts
- controlled manual receipt editing
- PDF receipt generation
- SMS notification abstraction and retries
- Manual Actions
- requisition lifecycle management
- automatic requisition assignment
- approval/rejection
- disbursement tracking
- multiple evidence files
- evidence verification
- requisition closure
- reporting/filtering/export
- immutable audit history
- historical record preservation
- automated tests for critical workflows

---

# 30. SOURCE AND IMPLEMENTATION STATUS

The functional requirements in this document are based on the validated project requirements document and the logical database model developed from those requirements.

Implementation decisions already established include:

- Django + PostgreSQL architecture
- modular monolith
- 25 core logical tables plus `requisition_attachments` identified during implementation validation
- exactly one seeded System Administrator
- administrator-created configurable roles, each containing a selected set of permissions
- System Users are assigned custom roles; System Administrator is never a selectable user role
- protected `.env` administrator recovery contacts
- strict transaction-phone contributor matching
- immutable automatic receipts
- soft deletion for manual receipts
- separate requested/approved/disbursed requisition amounts
- separate preferred/actual disbursement methods
- multiple evidence files per requisition
- append-only audit architecture
- provider abstraction
- SMS abstraction
- background processing with Celery/Redis

The coding AI should treat these as the current implementation baseline.

# END OF DOCUMENT
