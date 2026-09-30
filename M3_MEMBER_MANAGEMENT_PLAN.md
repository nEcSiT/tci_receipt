# Milestone 3 (M3) — Member Management Implementation Plan

## Objective
Build and validate the complete **Member Management** module for TCI Higher Life Center Financial Management System, strictly adhering to the Product Requirements Document (PRD Section 5) and Engineering Implementation Specification (Section 7). Establish a rock-solid domain foundation for member lifecycle, phone conflict enforcement, strict phone-based contributor identification, temporary contributor review/conversion/linking, and System Administrator-controlled member merges before building automated contributions and receipts on top of it.

---

## 1. Domain Architecture & Specifications

### 1.1 Member Entity (`Member`)
- **System-Generated Member ID**: Format `MEM-000001` via `IdGenerator.generate_member_number()`.
- **Attributes**:
  - `first_name` (required, max 100)
  - `middle_name` (optional, max 100)
  - `last_name` (required, max 100)
  - `email` (optional, max 255, indexed)
  - `ministry` (optional, e.g. "Youth Ministry", "Choir", "Ushering")
  - `team` (optional, e.g. "Protocol", "Media", "Technical")
  - `department` (optional, e.g. "Finance", "Welfare", "Missions")
  - `status`: `ACTIVE`, `INACTIVE`, `DEACTIVATED` (default `ACTIVE`)
- **Immutability & Historical Preservation**:
  - Deactivation flips `status = DEACTIVATED` without cascading or destroying historical contributions, receipts, or audit records.

### 1.2 Phone Number Management (`MemberPhoneNumber`)
- **Multiple Phones**: A member can have multiple associated phone numbers.
- **Strict Active Uniqueness**: An active phone number (`is_active=True`) cannot belong to more than one member.
- **Single Active Primary**: Exactly one active phone can be marked `is_primary=True` per member.
- **Inactive Numbers**: Inactive numbers (`is_active=False`) are retained for historical audit/matching logs and do not block active assignment to another member.
- **Standardization**: Phone numbers are normalized (strip whitespace, retain canonical format).

### 1.3 Strict Identification Foundation
- **Core Business Rule (PRD 5.4)**: Automatic contributions identify existing members **strictly by the transaction phone number**.
- **Negative Invariants**:
  - Provider name, contributor name similarity, email, or account reference must **never** be used to override or infer member identity.
- **Resolution Flow**:
  1. Incoming phone number searched in active `MemberPhoneNumber` records.
  2. If active match found -> Return matched `Member`.
  3. If no active match found -> Lookup or create `TemporaryContributor` with status `UNVERIFIED`.

### 1.4 Temporary Contributor Lifecycle (`TemporaryContributor`)
- **Attributes**: `phone_number`, `provider`, `provider_name`, `provider_account_reference`, `first_name`, `last_name`, `status`, `converted_member`.
- **Statuses**: `UNVERIFIED` -> `UNDER_REVIEW` -> `LINKED` or `CONVERTED`.
- **Workflows**:
  - **Review**: Mark status as `UNDER_REVIEW` while verifying identity.
  - **Link to Existing Member**: Reassigns contributions to target `Member`, optionally adds phone number if no conflict exists, marks status `LINKED`, and links `converted_member`.
  - **Convert to New Member**: Creates new `Member` with `first_name`, `last_name`, and adds phone number as primary, reassigns contributions, marks status `CONVERTED`.

### 1.5 Member Linking & Merging (`MemberMergeRequest`)
- **Workflow**:
  1. **Initiation**: Any user with `member.merge.request` permission (or System Administrator) can submit a merge request specifying `source_member`, `target_member`, and `reason`.
  2. **Validation**: Source and target cannot be the same member.
  3. **Strict Approval Boundary**: Only the **System Administrator** (`is_system_administrator`) can approve or reject the request.
  4. **Merge Execution (Atomic Transaction)**:
     - Reassign all `Contribution` records from `source_member` to `target_member`.
     - Move non-conflicting active `MemberPhoneNumber` records from source to target (marking as non-primary).
     - Deactivate `source_member` (`status = DEACTIVATED`).
     - Mark `MemberMergeRequest` as `APPROVED` with `reviewed_by` and `reviewed_at`.
     - Emit comprehensive immutable audit records (`MEMBER_MERGE_APPROVED`, `MEMBER_DEACTIVATED`, `CONTRIBUTIONS_REASSIGNED`).

---

## 2. Implementation Work Breakdown

### Step 1: Member Service Layer (`apps/members/services.py`)
- Implement `MemberService`:
  - `create_member(creator, first_name, last_name, middle_name, email, ministry, team, department, phone_number, provider)`
  - `update_member(actor, member, first_name, middle_name, last_name, email, ministry, team, department)`
  - `deactivate_member(actor, member, reason)`
  - `activate_member(actor, member)`
  - `add_phone_number(actor, member, phone_number, provider, is_primary)`
  - `set_primary_phone(actor, member, phone_id)`
  - `toggle_phone_status(actor, phone_id, is_active)`
- Implement `ContributorIdentificationService`:
  - `identify_contributor(phone_number, provider, provider_name, provider_account_reference)` -> strictly returns `(member, temporary_contributor)`
- Implement `TemporaryContributorService`:
  - `mark_under_review(actor, temporary_contributor)`
  - `link_to_member(actor, temporary_contributor, target_member, add_phone)`
  - `convert_to_member(actor, temporary_contributor, first_name, last_name, email, ministry)`
- Implement `MemberMergeService`:
  - `request_merge(requester, source_member, target_member, reason)`
  - `review_merge(reviewer, merge_request, approve, remarks)`

### Step 2: URL Routing & Views (`apps/members/views.py`, `apps/members/urls.py`)
- URLs under `/members/`:
  - `GET /members/` — Member list with search & status filters (`MemberListView`)
  - `GET, POST /members/create/` — Register new member (`MemberCreateView`)
  - `GET /members/<uuid:member_id>/` — Member profile & history (`MemberDetailView`)
  - `GET, POST /members/<uuid:member_id>/edit/` — Edit member details (`MemberEditView`)
  - `POST /members/<uuid:member_id>/phones/` — Add phone number (`MemberPhoneAddView`)
  - `POST /members/<uuid:member_id>/phones/<uuid:phone_id>/primary/` — Set primary phone
  - `POST /members/<uuid:member_id>/phones/<uuid:phone_id>/toggle/` — Toggle phone active status
  - `POST /members/<uuid:member_id>/deactivate/` — Deactivate member
  - `GET /members/temporary/` — List temporary contributors (`TemporaryContributorListView`)
  - `GET, POST /members/temporary/<uuid:tc_id>/link/` — Link temporary contributor to member
  - `GET, POST /members/temporary/<uuid:tc_id>/convert/` — Convert temporary contributor to new member
  - `GET, POST /members/merges/` — List merge requests & create request (`MemberMergeListView`, `MemberMergeCreateView`)
  - `POST /members/merges/<uuid:request_id>/review/` — System Administrator approve/reject merge

### Step 3: Frontend Templates (TCI Higher Life Center Design System)
- `templates/members/member_list.html`: Responsive table, search bar (number/name/phone/email), status badges, pagination, quick actions.
- `templates/members/member_detail.html`: Two-column layout:
  - Left column: Member ID card, contact info, ministry tags, status badge, action buttons.
  - Right column: Multiple phone numbers manager with primary indicator and add-phone modal/inline form; Contribution history table foundation; Merge requests history.
- `templates/members/member_form.html`: Branded form with validation error styling.
- `templates/members/temporary_list.html`: Unverified contributors queue with provider details and action buttons.
- `templates/members/merge_list.html`: Pending and historical merge requests with System Administrator action buttons.

### Step 4: Comprehensive Test Suite (`tests/test_milestone3.py`)
- Automated tests covering `M3-BT-001` through `M3-BT-014`:
  1. Creation with system ID & initial phone
  2. Multiple phones and single active primary enforcement
  3. Active phone conflict rejection across members
  4. Inactive phone availability
  5. Search across all fields
  6. Strict transaction phone contributor identification
  7. Temporary contributor creation on unknown phone
  8. Converting temporary contributor to member
  9. Linking temporary contributor to existing member
  10. Member merge request initiation
  11. System Administrator merge approval & contribution reassignment
  12. Non-administrator merge approval blocked
  13. Historical preservation upon deactivation
  14. Navigation & role-based permission enforcement

### Step 5: Engineering Specification Update
- Update `TCI_HLC_Engineering_Implementation_Specification.md` to document Milestone 3 as In Progress / Completed.
