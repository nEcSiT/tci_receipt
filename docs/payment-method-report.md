# Payment-method reporting — 4 November 2026

Owner: Nicholas Dornyo / Codex implementation session.
Branch: `feat/payment-method-dashboard` (based on main `9610627`).
Requirements: baseline PRD §18.1, §18.2 and permission-aware exports in §18; contributes to issue #17 (M8).

## Delivered behavior

From Dashboard → View payment breakdown, authorized report viewers can see confirmed contribution counts and amounts grouped by payment method and currency. Filter by contribution date (inclusive start/end), contribution type, entry method, payment method and currency. Blank dates mean all time. Inactive contribution types remain selectable for historical reporting.

Click a payment-method row to inspect the matching contributions on the same page. Results are paginated at 25 records; pagination does not affect aggregate totals. CSV exports contain the same filtered group counts and amounts. Invalid filters produce a validation error rather than silently widening the report.

Access requires `report.view`; CSV additionally requires `report.export`. The System Administrator has these permissions implicitly. New report access does not grant contribution editing or other actions.

Only confirmed contributions with either no linked gateway transaction or a successful linked transaction count toward received funds. Pending, cancelled, flagged, failed and duplicate gateway entries are excluded. Currency totals are never combined. No schema migration or new dependency is required.

## Scope and remaining work

This is one completed reporting slice, not the whole M8 milestone. PDF/Excel exports, other report families, global search and the remaining dashboard metrics are still outstanding. The original dashboard summary cards retain their prior behavior; this report does not validate or replace them.

GitHub review on 4 November 2026 found:

- M1/M2 foundation and authentication: merged in PR #10.
- M3/M4 members and contributions: merged in PR #25.
- M5/M6 receipts, automatic payments and notifications: open PR #26 at `cc7b9f7`; not merged into main.
- M5/M6 security changes: PR #30 merged into the feature branch; production provider integration and regression remain outstanding.
- M7 requisitions: models exist, but no complete portal/service/view workflow; issue #16 remains open.
- M8: report export helpers exist, but complete reporting UI and search are outstanding; this branch adds payment-method reporting.
- M9/M10, end-to-end UAT and production readiness remain outstanding.

Recommended next baseline feature: M7 requisitions, followed by the remaining M8 reports. Review and validate PR #26 before merging; do not treat its historical 116-test result as evidence for later commits.

User clarification recorded on 4 November: peer review is optional and requested by the document owner; the reviewer can directly edit only. This does not introduce an approval role. Peer editing is deferred while validated baseline features are built. Welfare, pledges, bulk entry and incident alerts still have open business decisions in the PRD review draft. Shepherding is outside this project.

## Validation

The 11 new tests cover currency reconciliation, excluded financial states, all five payment methods, manual/automatic filtering, historical contribution dates and types, invalid filters, permissions, CSV parity, drill-down and pagination.

The broader suite initially exposed an existing stale M2 assertion: PR #31 changed the protected-role error wording, but the test still expected the old wording. The assertion now matches the current validation and also verifies that no attempted administrator account is created.

Local regression uses SQLite, Django's in-memory cache in place of unavailable Redis, and test-only administrator/recovery settings. These overrides are confined to the test process. Redis, PostgreSQL, real payment/SMS providers and production deployment are not validated by this run. Django system checks pass.

Final result: **91 passed**, including all 11 new reporting tests. The 32 warnings concern an absent collected-static directory in this development checkout. This is local regression evidence for this main-based branch only, not for PR #26.
