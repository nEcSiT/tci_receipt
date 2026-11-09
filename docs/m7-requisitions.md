# M7 staged implementation

M7 is **not complete**. This branch implements requester drafts/supporting files,
submission, staff queue/locking, and approval/rejection. Reassignment, payouts,
expenditure evidence, verification and closure await the decisions below.

## Baseline and preservation

Inspected branch: `feature-branch`, clean, four commits ahead of its fetched remote.
The remote is an ancestor of local commit `02bda75`; no merge/reset/stash was needed.
Created `feat/m7-requisitions` from that commit. Local branding and USSD test upgrades,
PR #33 reporting, and existing M1–M6 code remain in its ancestry. No main changes,
push, deployment or PR were performed. No applicable AGENTS.md was found.

Sources: PRD §§12–17, related security/audit/notification rules, engineering
specification requisition schema/workflows, TCI-HLC design §18, and
https://github.com/nEcSiT/tci_receipt/issues/16 (open, no comments).

Initial gaps: requisition models/migration/basic tests existed but no workflow
services, routes, portal or staff screens. Drafts could not omit amounts/required
text; rejected records could still change fields; current assignments lacked a
database uniqueness constraint. Existing permissions, ID sequence, audit models,
signed receipt access and notification retries were reusable. Existing local storage
is normally under public MEDIA_ROOT, so requisition files use a separate private root.

## Stage 1 — requester flow

For an isolated development instance, install project dependencies, apply migrations,
set `REQUISITION_PORTAL_ENABLED=True`, and visit `/requisitions/request/`.
Do not enable this in production before access policy is agreed.

Create draft → retain private link → save incomplete data → upload optional supporting
documents → complete required fields → submit. The same private link displays limited
status after submission. Names/phones/reference numbers never authorize access.
There is no public member directory or public member-linking operation.

Creation uses a signed form nonce and the existing sequence lock; repeated/concurrent
POSTs reuse the same draft. Saving/submission locks its row. Submission is idempotent,
records one audit event/time, preserves REQ number, and freezes requester edits.
SUBMITTED is directly eligible for Start Working; legacy PENDING_REVIEW is also
accepted. There is no stranded waiting state or required APPROVED waiting stage.

Required at submission: requester name/phone, purpose, description, positive Decimal
amount (12 digits, 2 decimal places). Date and preferred method remain optional as in
the schema. Provided phone values use existing trim-only normalization plus a technical
7–15 digit format check, optionally prefixed by +; no automatic member matching occurs.
Priority has no dedicated field/taxonomy; optional remarks remain available.

### Access proposal (pending confirmation)

Signed, purpose-scoped bearer link shown directly after draft creation, configurable
30-day lifetime. No SMS delivery is assumed for an incomplete draft. Link issuance
uses a per-record revocation version; rotating that UUID invalidates prior links.
There is no public link reissue/recovery API. Lost/expired links require contact with
the church; identity verification and authorized recovery are not yet implemented.
Link possession is authorization, not verification of identity or phone ownership.

Portal responses disable caching/indexing and cross-origin referrers and use no third-party assets.
Same-origin referrers remain enabled for Django's browser CSRF checks.
Deployment access logs must redact `/requisitions/request/` token paths, as with other
bearer-link routes; raw links must not be copied into audit records or support tickets.

### Files

Defaults: 5 MiB/file, ten supporting documents/request. These are technical limits,
not church policy. Configure REQUISITION_UPLOAD_MAX_BYTES/MAX_COUNT. PDF/JPG/JPEG/PNG/TXT
are the only accepted types; parsed PDFs reject actions, forms, annotations and
embedded files. Images are decoded/verified and rewritten without original metadata;
PDFs are rewritten from the validated document. Text must be UTF-8 without markup or
binary controls. Malformed/active content fails closed. Complex PDFs may be rejected.

Files are stored under `BASE_DIR/private_requisitions` (never expose this via a web
server). Random storage keys and scoped attachment downloads prevent ID guessing and
traversal. Responses force attachment/octet-stream and nosniff. Metadata preserves
the original filename, uploader context and stored size/type. Upload batches validate
before writing and remove written files on ordinary transaction failures. A process
crash can still require orphan-file reconciliation; no background reconciler exists.
Supporting files are separate from future expenditure evidence.

## Stage 2 — queue and assignment

`/requisitions/` requires requisition.view; supports search, status/date filters,
my-work/unassigned filters and pagination. Drafts are excluded. Queue rows reveal
only their summary and handler, not phone/payment details or attachment links.
Direct detail/download access enforces the assignment lock even for administrators.

Start Working requires requisition.review, locks the requisition row, creates one
current assignment and moves to UNDER_REVIEW. Repeated clicks by the handler are safe.
A conditional database unique constraint independently protects current assignments.
Migration refuses existing duplicate current assignments rather than erasing history;
such legacy data requires a reviewed remediation. Reassignment/unlock UI is absent
pending explicit authority. Cross-role handoffs are not silently bypassed.

## Stage 3 — decisions

The active handler must have requisition.approve. Approval validates a positive amount,
preserves requested amount and records decision/history before PENDING_DISBURSEMENT.
No requested-amount ceiling is invented. Rejection requires a nonblank reason and
permanently freezes ordinary model saves. Opposing concurrent decisions cannot both win.
Retries of an already-recorded matching decision return without mutation or another SMS.

Notifications are recorded in the decision transaction and dispatched after commit
through the existing SMS provider/retry service. Transport exceptions become failed
attempts; they cannot roll back the financial decision. Pending messages also survive
process failure before the post-commit callback. Run:

```
python manage.py retry_requisition_notifications
```

This command retries only workflow notifications, never financial mutations. A scheduler
must invoke it operationally; no new scheduler is automatically deployed. Existing retry
escalation classifies exhausted requisition SMS as a generic manual action, preserving
the existing thank-you classification for contribution notifications. Delivery is at-least-once: a provider
acceptance followed by a crash can cause a duplicate SMS, but not a duplicate decision.

## Outstanding decisions / incomplete workflows

1. Confirm draft link delivery/lifetime, identity verification and recovery/revocation authority.
2. Proposed `requisition.reassign` and `requisition.close` permissions: confirm who receives
   these and explicit recorded handoffs across reviewers/approvers/disbursers/verifiers.
3. Are approvals allowed above requested amount? No ceiling is currently imposed.
4. Single final payout or installments? Cumulative limit, unused balance handling and
   whether a below-approval payout is final remain unspecified. No payout endpoint exists.
5. Proposed closure: an explicitly authorized closer attests evidence sufficiency after
   current evidence is reviewed. Confirm complete-set/replacement rules; a single verified
   file must not silently close the request. Evidence portal/review/closure are not built.
6. Cash acknowledgement/signature policy and permitted proof format.
7. Preferred-method detail requiredness; no new priority taxonomy is imposed.
8. Confirm configurable upload/link technical defaults before public enablement.

Optional verified member linkage, authorized recovery and production log-redaction
configuration remain incomplete. No real payment transfer integration is introduced.

## Validation

Baseline `.venv/bin/pytest -q`: **128 passed**, 497.22 seconds; one existing WeasyPrint
HarfBuzz deprecation warning. Full changed-tree regression and final browser results
are recorded below after completion. Development uses in-memory cache, not real Redis;
SMS uses the existing mock provider. SQLite does not validate row locks.

Initial Stage 1: `.venv/bin/pytest tests/test_m7_requester.py tests/test_requisitions.py -q`
→ **20 passed**. Expanded PostgreSQL tests (isolated Django test database):

```
DATABASE_URL=postgres://tci_user:tci_password@127.0.0.1:5432/tci_hlc_finance .venv/bin/pytest tests/test_m7_requester.py tests/test_m7_assignment.py tests/test_m7_decisions.py -q
```

→ **23 passed**, including concurrent creation/submission, Start Working and opposing
decisions. One pypdf test-fixture deprecation warning. This is local Compose test
configuration; do not copy its credentials to production. Disbursement concurrency
cannot be claimed: that workflow is not implemented.

`python manage.py check` and `python manage.py makemigrations --check --dry-run` pass.
Migrations: requisitions 0002 (nullable drafts/access metadata), 0003 (current assignment
constraint), audit 0002 (explicit requester actor). Existing identifiers are never rewritten.
