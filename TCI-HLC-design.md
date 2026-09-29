# TCI Higher Life Center — Digital Product Design System

**Version:** 1.0  
**Product:** TCI Higher Life Center Receipt & Financial Management System  
**Design status:** Implementation-ready foundation  
**Primary audience:** System Administrator, System Users, finance/administrative staff, and external requisition requesters  
**Design direction:** Trustworthy, warm, modern, ministry-centered, operationally clear

---

## 1. Design Foundation

The TCI Higher Life Center digital product should translate the existing church identity into a modern financial and administrative interface. The brand guide establishes a palette built around deep navy, green, orange, yellow, and light blue, with **Cabin** and **Libre Baskerville** as the primary typefaces.

The application is not a marketing website. It is a financial/administrative system. Therefore, the visual language should preserve TCI's warmth and Christian identity while prioritizing:

- financial accuracy
- clarity of actions
- trust
- traceability
- accessibility
- fast scanning of records
- strong hierarchy
- restrained use of decorative elements

The interface should feel like **TCI**, not like a generic banking dashboard and not like a corporate SaaS template.

### Core design principles

1. **Trust before decoration** — financial information must be visually calm and credible.
2. **Clarity before density** — users should understand what they are looking at before interacting with it.
3. **Brand through hierarchy** — TCI colors and typography should be visible without making every element colorful.
4. **Orange is emphasis, not noise** — use the strongest warm accent for important calls to action and branded moments.
5. **Navy establishes authority** — use it for navigation, major headers, important surfaces, and identity.
6. **Green communicates positive progress** — use it for successful/verified states and selected positive actions.
7. **Yellow communicates attention** — use it for pending or attention-required states, not errors.
8. **Light blue supports information** — use it for secondary highlights, information states, and supporting UI.
9. **White and neutral surfaces carry the application** — the operational interface should remain predominantly light and readable.
10. **Every destructive or irreversible action must be unmistakable.**

---

# 2. Brand Palette

The supplied TCI branding shows five core colors. The sampled brand artwork corresponds approximately to the following digital values:

| Token | Hex | RGB | Primary role |
|---|---|---|---|
| `brand-navy` | `#011345` | `1, 19, 69` | Primary brand, navigation, headers, strong text on brand surfaces |
| `brand-green` | `#4ECB4D` | `78, 203, 77` | Positive action, success, growth, verified states |
| `brand-orange` | `#F16822` | `241, 104, 34` | Primary CTA, warmth, emphasis, key branded highlights |
| `brand-yellow` | `#FEC313` | `254, 195, 19` | Attention, pending states, secondary highlights |
| `brand-blue` | `#51ACFD` | `81, 172, 253` | Information, secondary actions, supporting highlights |

> **Important:** The TCI branding PDF visually establishes the palette but does not provide formal hexadecimal specifications. The digital values above are therefore treated as implementation-ready sampled approximations and should be replaced by official brand color codes if TCI supplies them.

### Neutral palette

The brand colors should be supported by a restrained neutral system:

| Token | Hex | Use |
|---|---|---|
| `white` | `#FFFFFF` | Cards, forms, primary content surfaces |
| `surface` | `#F7F8FA` | Application background |
| `surface-subtle` | `#F1F3F6` | Secondary panels, table headers, disabled backgrounds |
| `text-primary` | `#101828` | Main text |
| `text-secondary` | `#475467` | Supporting text |
| `text-muted` | `#667085` | Metadata, timestamps, helper text |
| `border` | `#D0D5DD` | Inputs, tables, cards, dividers |
| `border-strong` | `#98A2B3` | Focused/stronger structural borders |
| `overlay` | `#011345CC` | Modal and image overlays |

### Semantic states

Semantic colors must remain distinct from the brand palette while harmonizing with it.

| Token | Hex | Meaning |
|---|---|---|
| `success` | `#16803C` | Successful, verified, completed |
| `success-soft` | `#EAF7EE` | Success background |
| `warning` | `#A15C00` | Attention required, pending |
| `warning-soft` | `#FFF6D8` | Warning background |
| `error` | `#B42318` | Failed, rejected, destructive |
| `error-soft` | `#FEECEB` | Error background |
| `info` | `#1769AA` | Informational state |
| `info-soft` | `#EAF4FD` | Information background |

Semantic colors must never replace the TCI brand palette for general decoration.

---

# 3. Color Usage Rules

## 3.1 Navy

`brand-navy` is the visual anchor of the system.

Use it for:

- application sidebar
- top-level navigation
- login/authentication backgrounds
- page headers when a strong branded treatment is required
- primary headings on selected dark surfaces
- receipt identity areas
- high-trust system areas
- selected navigation states
- dark footer areas

Do not use navy as the background of every card or section. The application should breathe through white and light-neutral surfaces.

### Navy surface hierarchy

- `brand-navy` — strongest brand surface
- navy at approximately 90% opacity — overlays and secondary dark surfaces
- navy at approximately 6–10% opacity — subtle tinted backgrounds where needed

## 3.2 Orange

`brand-orange` is the primary expressive accent.

Use it for:

- primary CTA buttons
- important action links
- selected dashboard highlights
- branded welcome statements
- key visual emphasis
- active progress indicators when appropriate

Avoid using orange for:

- every button
- long blocks of body text
- table rows
- large page backgrounds
- error states

Orange should make the important action obvious.

## 3.3 Green

`brand-green` communicates positive movement and ministry growth.

Use it for:

- successful contribution indicators
- verified evidence
- completed requisitions
- successful notification delivery
- active/healthy system indicators
- positive statistics
- confirmation icons
- selected secondary CTAs where orange is already dominant

Green should not be used as the only indication of success; pair it with text or an icon.

## 3.4 Yellow

`brand-yellow` is an attention color.

Use it for:

- pending status
- awaiting review
- manual action required
- delayed payment confirmation
- attention banners
- warning badges

Do not use yellow for ordinary decorative accents throughout the interface.

## 3.5 Light Blue

`brand-blue` is the information/support accent.

Use it for:

- informational cards
- system guidance
- help panels
- secondary metrics
- links where appropriate
- informational badges
- non-critical system notices

---

# 4. Brand Color Proportion

The application should visually follow this approximate distribution:

- **60–70%:** white and neutral surfaces
- **15–25%:** navy
- **5–10%:** orange
- **2–5%:** green
- **1–3%:** yellow
- **1–3%:** light blue

These are design targets rather than strict mathematical requirements.

The goal is to prevent the dashboard from becoming a multicolor interface. The five brand colors should work as a hierarchy, not as competing decorations.

---

# 5. Typography

The TCI branding guide presents **Cabin** and **Libre Baskerville** as the type families.

## 5.1 Cabin

Cabin is the primary interface typeface.

Use Cabin for:

- navigation
- buttons
- form labels
- body copy
- tables
- metadata
- dashboard metrics
- system messages
- page titles where an operational tone is preferred

Recommended weights:

- Regular — body text
- Medium — navigation and supporting headings
- SemiBold — headings, buttons, important labels

## 5.2 Libre Baskerville

Libre Baskerville is the editorial/brand voice.

Use it selectively for:

- authentication welcome statements
- major page introductions
- church/ministry messaging
- receipt appreciation messages
- empty-state encouragement where appropriate
- major marketing or public-facing moments

Do not use Libre Baskerville for dense tables, forms, navigation, or long operational content.

### Typography principle

**Cabin runs the application. Libre Baskerville gives it personality.**

---

# 6. Type Scale

| Token | Font | Size | Weight | Line height | Primary use |
|---|---|---:|---:|---:|---|
| `display-xl` | Libre Baskerville | 48px | 700 | 58px | Login/welcome hero, major public statement |
| `display-lg` | Libre Baskerville | 40px | 700 | 50px | Major branded heading |
| `heading-xl` | Cabin | 32px | 600 | 40px | Page title |
| `heading-lg` | Cabin | 28px | 600 | 36px | Major section heading |
| `heading-md` | Cabin | 24px | 600 | 32px | Card/section heading |
| `heading-sm` | Cabin | 20px | 600 | 28px | Small section heading |
| `body-lg` | Cabin | 18px | 400 | 28px | Introductory text |
| `body-md` | Cabin | 16px | 400 | 24px | Default body/UI text |
| `body-sm` | Cabin | 14px | 400 | 20px | Supporting text |
| `caption` | Cabin | 12px | 400 | 18px | Metadata |
| `label-lg` | Cabin | 16px | 600 | 24px | Important labels/buttons |
| `label-md` | Cabin | 14px | 600 | 20px | Default labels/buttons |
| `label-sm` | Cabin | 12px | 600 | 18px | Compact labels |

Do not use excessive all-caps text. Sentence case is the default.

---

# 7. Spacing System

Use a consistent 8px base rhythm.

| Token | Value | Use |
|---|---:|---|
| `space-1` | 4px | Icon/text micro gap |
| `space-2` | 8px | Tight UI spacing |
| `space-3` | 12px | Form/control internal spacing |
| `space-4` | 16px | Default component gap |
| `space-5` | 20px | Compact grouping |
| `space-6` | 24px | Card padding / section grouping |
| `space-8` | 32px | Major component gap |
| `space-10` | 40px | Section spacing |
| `space-12` | 48px | Large section spacing |
| `space-16` | 64px | Page-level separation |
| `space-20` | 80px | Major branded sections |

The operational dashboard should generally use 24px card padding and 32–40px spacing between major sections.

---

# 8. Layout System

## Desktop

Primary application layout:

```text
┌─────────────────────────────────────────────────────────────┐
│ Sidebar │ Header                                            │
│         ├───────────────────────────────────────────────────┤
│         │ Page title / breadcrumbs                          │
│         │                                                   │
│         │ Main content                                      │
│         │                                                   │
│         │ Cards / tables / forms                            │
└─────────┴───────────────────────────────────────────────────┘
```

### Sidebar

- background: `brand-navy`
- logo: white/light version where available
- primary navigation text: white with controlled opacity
- active navigation: orange indicator or orange-tinted active surface
- iconography: white/neutral
- System Administrator-only items must be permission-aware

### Header

Use a white header for operational pages.

Contains:

- page context/breadcrumbs
- search where relevant
- notifications
- current user
- user menu

The header should not compete with the navy sidebar.

## Tablet

- retain sidebar where space allows
- collapse to icon/overlay navigation below the desktop breakpoint
- preserve touch targets
- tables become horizontally scrollable rather than forcing unreadable columns

## Mobile

The application should remain usable rather than simply shrink.

- sidebar becomes a drawer
- dashboard cards stack
- tables transform into cards where practical
- forms use one column
- primary actions remain prominent
- destructive actions require confirmation

---

# 9. Navigation

Recommended primary navigation:

1. Dashboard
2. Contributions
3. Receipts
4. Members
5. Requisitions
6. Notifications
7. Reports
8. Manual Actions
9. Audit Logs — permission restricted
10. Users & Roles — System Administrator only

Navigation visibility must follow permissions, but hiding a menu item is not an authorization mechanism. Backend authorization remains authoritative.

### Active navigation

Preferred treatment:

- orange left indicator or orange accent
- subtle white/orange-tinted background
- semibold Cabin label

Avoid filling every active item with bright orange; the navigation should remain predominantly navy.

---

# 10. Buttons

Buttons should be rounded, confident, and easy to scan. TCI's visual identity supports a softer rounded control language, but operational controls should remain more restrained than marketing CTAs.

## Primary button

```text
Background: brand-orange
Text: white
Font: Cabin SemiBold 14px
Height: 44–48px
Horizontal padding: 20–24px
Radius: 10px
```

Use for:

- Create Contribution
- Issue Receipt
- Create Requisition
- Approve
- Save
- Submit

## Secondary button

```text
Background: brand-navy
Text: white
Border: none
Radius: 10px
```

Use for secondary high-value actions.

## Outline button

```text
Background: transparent
Text: brand-navy
Border: 1px solid border-strong
Radius: 10px
```

Use for:

- Cancel
- Filter
- Export
- View Details

## Success action

Use green sparingly for actions that explicitly confirm a positive state, such as **Verify Evidence**.

## Destructive button

```text
Background: error
Text: white
```

Use for:

- Deactivate User
- Delete/soft-delete Manual Receipt
- Reject where the action is destructive

### Button rules

- Minimum touch target: 44px
- One dominant CTA per area
- Do not place two equally strong colored buttons beside each other unless both are genuinely primary alternatives
- Use loading states during asynchronous operations
- Never allow double submission

---

# 11. Cards

Cards should provide structure without making the dashboard look like a collection of floating boxes.

```text
Background: white
Border: 1px solid #D0D5DD
Radius: 12px
Padding: 24px
Shadow: none or extremely subtle
```

### Metric cards

Use a small brand accent rather than a full-color background.

Example:

```text
┌────────────────────────────┐
│ Contributions              │
│                            │
│ GHS 24,500.00              │
│ +12.4% this month          │
└────────────────────────────┘
```

Recommended accent mapping:

- Contributions — green
- Receipts — orange
- Requisitions — blue
- Pending actions — yellow

The accent should appear as an icon, small top border, or metric indicator—not necessarily as the whole card background.

---

# 12. Forms and Inputs

Inputs should be highly readable and calm.

```text
Background: white
Border: 1px solid #D0D5DD
Text: text-primary
Radius: 10px
Height: 44–48px
Padding: 12px 14px
```

Focused state:

- border: `brand-blue` or `brand-navy`
- visible focus ring

Error state:

- border: `error`
- error icon/text
- clear explanation

### Form structure

Use:

```text
Label
Helper text (when needed)
Input
Validation message
```

Do not use placeholder text as the only label.

### Financial inputs

Amounts should:

- clearly show currency
- use right-aligned numeric presentation where appropriate
- preserve decimal precision
- prevent accidental negative values
- display formatted values after entry where appropriate

---

# 13. Tables

Financial records require strong table design.

### Table principles

- white surface
- light borders
- strong column labels
- 14px Cabin
- row height approximately 52–60px
- right-align monetary values
- consistent date/time formatting
- status displayed with text + semantic badge
- actions grouped in a final column

### Table header

```text
Background: surface-subtle
Text: text-secondary
Font: Cabin SemiBold 12–14px
```

Avoid dark navy table headers unless a particular report/export design calls for it.

### Row interaction

Hover may use a very light navy tint.

Selected rows may use a light blue or navy-tinted background.

---

# 14. Status Badges

Badges should communicate state immediately without dominating the screen.

| Status type | Background | Text |
|---|---|---|
| Success | `success-soft` | `success` |
| Pending | `warning-soft` | `warning` |
| Error/Rejected | `error-soft` | `error` |
| Information | `info-soft` | `info` |
| Neutral | `surface-subtle` | `text-secondary` |

Use labels such as:

- Successful
- Pending
- Failed
- Cancelled
- Duplicate
- Approved
- Rejected
- Under Review
- Disbursed
- Evidence Submitted
- Verified
- Completed
- Inactive

Do not communicate financial state with color alone.

---

# 15. Dashboard Design

The dashboard is the primary operational overview.

## Header

```text
Good morning, Nicholas
Here's what's happening across TCI Higher Life Center today.
```

Use Cabin for operational text. A small Libre Baskerville statement may be used for a branded welcome, but it should not interfere with the operational hierarchy.

## Summary area

Recommended cards:

- Today's Contributions
- Receipts Generated
- Pending Requisitions
- Manual Actions

## Recent activity

Show:

- event
- person/system
- time
- status
- related record

## Action-required section

This section should visually prioritize:

- failed receipt generation
- failed notification
- merchant transactions
- duplicate contribution review
- evidence awaiting verification
- other manual actions

Use yellow/amber for attention rather than red unless the event is actually an error or failure.

---

# 16. Contribution UI

The contribution experience should be fast and confidence-building.

### Contribution creation

Sections:

1. Contributor
2. Contribution type
3. Amount
4. Payment method
5. Reference/details
6. Review
7. Confirmation

### Contribution type cards

Use restrained brand accents:

- Tithe — navy/green
- Thanksgiving — orange
- Higher Life Partners — blue
- Building Project — yellow/orange
- Other — neutral/blue

These are UI accents only; they do not change the underlying financial meaning.

### Automatic contribution status

Show a clear processing timeline:

```text
Payment received
      ↓
Member identified
      ↓
Contribution recorded
      ↓
Receipt generated
      ↓
Thank-you sent
```

Each step should have a state: pending, successful, failed, or not applicable.

---

# 17. Receipt UI

Receipts are high-trust financial artifacts and should have a visual identity distinct from the dashboard.

## Receipt screen

Use:

- white document surface
- TCI logo at the top
- navy identity/header area
- orange accent line or highlight
- clear receipt number
- contributor name
- contribution type
- amount
- payment mode
- date
- relevant reference
- Generated By

**Never display the contributor's phone number on the receipt.**

### Receipt number

Display prominently:

`HLC-5831047`

Use Cabin SemiBold or Libre Baskerville sparingly for the receipt identifier.

### Receipt status

Automatic receipts should show an immutable indicator where appropriate:

> System-generated • Final

Manual receipts may show:

> Issued by [System User]

### PDF visual direction

The PDF should feel like an official church financial document—not like a screenshot of the dashboard.

Recommended structure:

```text
┌────────────────────────────────────────────┐
│ TCI LOGO                     RECEIPT        │
│ Higher Life Center         HLC-XXXXXXX     │
├────────────────────────────────────────────┤
│ Contributor                                │
│ Nicholas Dornyo                            │
│                                            │
│ Contribution Type        Amount            │
│ Tithe                    GHS 500.00        │
│                                            │
│ Payment Mode              Date             │
│ Mobile Money             29 Sep 2026       │
├────────────────────────────────────────────┤
│ Thank you for your contribution.           │
│ Jesus Saves, Heals & Satisfies             │
└────────────────────────────────────────────┘
```

---

# 18. Requisition UI

Requisitions require more workflow clarity than decoration.

### Requisition detail layout

```text
Header
├── REQ-000125
├── Status
└── Primary action

Requester information
Request details
Financial summary
Assignment history
Approval history
Disbursement
Evidence
Audit/activity
```

### Financial summary

Always distinguish:

```text
Requested       GHS 5,000.00
Approved        GHS 4,000.00
Disbursed       GHS 3,500.00
Remaining       GHS   500.00
```

Never collapse these values into one generic amount.

### Workflow indicator

Use a horizontal stepper on desktop and a vertical stepper on mobile:

```text
Submitted → Review → Approved → Disbursed → Evidence → Verified → Closed
```

Rejected requisitions should clearly show the rejection reason and that they cannot be edited/resubmitted.

---

# 19. Notifications UI

Notifications should be treated as operational records.

Display:

- notification type
- recipient
- channel
- status
- attempts
- last attempt
- provider reference
- failure reason where applicable

### Notification status colors

- Sent/Delivered — green
- Pending/Sending — yellow
- Failed/Manual Action — red
- Informational — blue

A failed notification must not visually imply that the underlying contribution or administrative event was reversed.

---

# 20. Manual Actions

Manual Actions are an operational queue.

The screen should immediately answer:

1. What happened?
2. What record is affected?
3. What needs to be done?
4. Who owns it?
5. How urgent is it?
6. What has already been attempted?

Use priority badges:

- Critical — error/red
- High — orange
- Normal — blue
- Low — neutral

The list should support filtering by status, type, priority, assignee, and date.

---

# 21. Members & Contributors

Member records should emphasize identity accuracy.

### Member profile

```text
Member identity
├── Member number
├── Full name
├── Email
├── Phone numbers
├── Primary phone
├── Ministry
├── Team
└── Department

Contribution history
Receipt history
Merge/link history
Audit activity
```

Phone numbers should be presented as separate records rather than merged into one string.

Temporary contributors should have a clearly visible **Unverified** state.

---

# 22. Authentication Screens

Authentication is a major brand opportunity because it is visually simple and can carry more of the church identity.

## Login

Preferred composition:

```text
┌───────────────────────────────────────────────────┐
│                                                   │
│  Navy branded area        White login panel       │
│                                                   │
│  TCI logo                 Welcome back             │
│  Brand statement          Email                    │
│                          Password                  │
│  Jesus Saves,             Remember me              │
│  Heals & Satisfies        Sign in                  │
│                                                   │
└───────────────────────────────────────────────────┘
```

Use Libre Baskerville for the main welcome statement and Cabin for the actual form.

The login page should not look like a generic software login page.

---

# 23. System Administrator UI

The System Administrator has broader visibility and control, but the UI should remain consistent with System User screens.

Additional areas:

- Users
- Roles & Permissions
- Audit Logs
- System-wide Manual Actions
- Recovery/security configuration status

Do not visually label the System Administrator as a “Super Admin.” The product terminology is **System Administrator**.

Permission-restricted controls should be visually unavailable or hidden where appropriate, while backend authorization remains authoritative.

---

# 24. Audit Log UI

Audit records should be designed for traceability rather than visual decoration.

Columns:

- timestamp
- actor
- action
- entity
- entity ID
- result
- reason

Detail view:

```text
Who
What
When
Where
Old value
New value
Reason
Result
Provider reference
```

JSON old/new values may be shown in a readable expandable viewer.

Audit records should not expose secrets, passwords, OTP values, or protected recovery credentials.

---

# 25. Empty States

Empty states should be useful, not decorative.

Example:

### No contributions yet

**No contributions found**  
There are no contribution records matching the selected filters.

Primary action where appropriate: **Record Contribution**

Use a small line illustration/icon in navy or light blue. Do not use large colorful illustrations that distract from the task.

---

# 26. Loading States

Use skeletons for page-level data loading.

Use inline spinners for button actions.

Example:

```text
[ Generating receipt… ]
```

Never leave users uncertain whether an action was accepted.

For financial operations, show explicit completion confirmation.

---

# 27. Confirmation Dialogs

Use confirmation dialogs for irreversible or consequential actions.

Examples:

- Deactivate System User
- Delete/soft-delete manual receipt
- Reject requisition
- Reassign requisition
- Merge members

Dialog structure:

```text
Title
Short explanation
Consequences
Optional reason field
Cancel       Confirm action
```

Destructive confirmation buttons must use the error semantic color.

---

# 28. Toasts and System Messages

Use toasts for short-lived confirmation.

Examples:

- Contribution recorded successfully.
- Receipt generated successfully.
- User deactivated successfully.
- Evidence submitted successfully.

Persistent alerts should be used for issues requiring attention.

Do not use orange, yellow, and red interchangeably. Their meanings must remain predictable.

---

# 29. Icons

Use a single consistent icon family across the product.

Preferred characteristics:

- simple line icons
- 1.5–2px visual stroke
- rounded or softly geometric forms
- 20–24px default size

Icons should support labels rather than replace them in critical financial actions.

Brand color usage:

- navy — default
- orange — primary emphasis
- green — success
- yellow — warning
- blue — information
- white — dark/navy surfaces

---

# 30. Imagery and Branding

The church logo is a primary identity asset and should not be recreated using text.

Use the supplied official logo artwork wherever possible.

The existing branding shows the church emblem incorporating the globe, flame, and TCI identity, with **Triumphant Church International** and **Higher Life Center** naming. fileciteturn9file1L1-L10

The application should use:

- full logo on login and public-facing screens
- compact/appropriate logo variant in the sidebar where available
- monochrome/light logo variant on navy backgrounds where supplied

Do not stretch, rotate, recolor, or distort the logo.

---

# 31. Brand Statement Usage

The supplied branding includes:

**“Jesus Saves, Heals & Satisfies”**

Use this statement selectively:

- login page
- public-facing receipt appreciation area
- selected empty states
- official PDF/printed receipt footer where appropriate

Do not place the statement in every dashboard header or navigation area.

---

# 32. Accessibility

Accessibility is a product requirement, not a visual enhancement.

### Requirements

- WCAG-conscious color contrast
- visible keyboard focus
- minimum 44px interactive targets
- labels for all form fields
- no color-only status communication
- readable 14px minimum for dense UI text
- scalable text
- semantic HTML
- accessible dialogs
- keyboard-navigable tables and menus
- screen-reader labels for icon-only controls

### Color caution

The TCI palette contains bright colors that may not provide sufficient contrast when used as text on white. In particular:

- use orange primarily as a fill/accent rather than small text on white
- use yellow primarily as a background/accent, not as body text
- use green carefully for text and verify contrast in implementation
- use navy for high-contrast text on light surfaces

Always verify actual rendered contrast rather than assuming the brand color is accessible in every role.

---

# 33. Responsive Rules

### 1440px+

- full sidebar
- multi-column dashboard
- wide tables
- expanded filters

### 1024–1439px

- full or compact sidebar
- reduced dashboard columns
- tables may reduce secondary columns

### 768–1023px

- collapsible navigation
- two-column cards where useful
- filter panels become drawers

### <768px

- navigation drawer
- one-column forms
- stacked cards
- table-to-card transformation where practical
- sticky primary action only when it improves task completion

---

# 34. Motion

Motion should be subtle and functional.

Use:

- 150–200ms hover/focus transitions
- 200–300ms drawer/modal transitions
- short loading animations

Avoid:

- excessive page transitions
- bouncing buttons
- decorative animated backgrounds
- animated financial numbers that make values difficult to read

The system should feel calm and dependable.

---

# 35. Elevation and Borders

The design should be mostly flat.

Preferred hierarchy:

1. color contrast
2. spacing
3. borders
4. very subtle shadow only when required

Avoid:

- heavy drop shadows
- glassmorphism
- excessive gradients
- glowing UI
- floating cards everywhere

The TCI brand already has strong color and typography. Additional visual effects are unnecessary.

---

# 36. Data Visualization

Reports should use restrained charts.

Recommended chart palette order:

1. navy
2. orange
3. green
4. blue
5. yellow

Do not create charts where every data point receives a different bright color.

### Financial charts

Prefer:

- bar charts for contribution types
- line charts for contribution trends
- stacked bars for payment methods
- simple status distributions for requisitions

Charts must always have visible labels/legends and accessible alternatives.

---

# 37. Search and Filtering

Search should be visually prominent but not oversized.

Recommended control:

```text
[ 🔍 Search members, receipts, contributions... ]
```

Filters should use compact outlined controls.

Common filter groups:

- date
- member
- contribution type
- payment mode
- entry method
- user
- requisition status
- ministry/team/department/group
- notification status

Use a clear **Reset filters** action.

---

# 38. Financial Formatting

Currency:

`GHS 5,000.00`

Use consistent decimal precision for financial values.

Dates:

`29 Sep 2026`

Date/time where needed:

`29 Sep 2026, 09:42 AM`

Reference numbers should use a monospace treatment only where it improves scanability; otherwise use Cabin.

Never truncate receipt, contribution, requisition, or provider reference numbers where exact identification is required.

---

# 39. Design Tokens — Implementation Reference

```css
:root {
  /* TCI brand */
  --color-brand-navy: #011345;
  --color-brand-green: #4ECB4D;
  --color-brand-orange: #F16822;
  --color-brand-yellow: #FEC313;
  --color-brand-blue: #51ACFD;

  /* Neutral */
  --color-white: #FFFFFF;
  --color-surface: #F7F8FA;
  --color-surface-subtle: #F1F3F6;
  --color-text-primary: #101828;
  --color-text-secondary: #475467;
  --color-text-muted: #667085;
  --color-border: #D0D5DD;
  --color-border-strong: #98A2B3;

  /* Semantic */
  --color-success: #16803C;
  --color-success-soft: #EAF7EE;
  --color-warning: #A15C00;
  --color-warning-soft: #FFF6D8;
  --color-error: #B42318;
  --color-error-soft: #FEECEB;
  --color-info: #1769AA;
  --color-info-soft: #EAF4FD;

  /* Typography */
  --font-ui: "Cabin", sans-serif;
  --font-display: "Libre Baskerville", serif;

  /* Radius */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 20px;
  --radius-full: 9999px;

  /* Spacing */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;
  --space-16: 64px;
  --space-20: 80px;
}
```

---

# 40. Component Priority

Implementation should build the design system in this order:

### Foundation

1. color tokens
2. typography tokens
3. spacing
4. radius
5. focus states
6. responsive containers

### Core components

7. buttons
8. inputs
9. selects
10. badges
11. cards
12. tables
13. alerts
14. modals
15. dropdowns
16. pagination
17. breadcrumbs
18. tabs
19. steppers
20. skeleton/loading states

### Product components

21. dashboard metric card
22. contribution form
23. contribution status timeline
24. receipt preview
25. requisition workflow stepper
26. financial summary
27. evidence uploader
28. notification history
29. manual-action queue
30. audit event viewer

---

# 41. Screen-Level Design Rules

## Dashboard

Brand expression: **moderate**  
Operational density: **medium**

## Contributions

Brand expression: **low–moderate**  
Operational density: **high**

## Receipts

Brand expression: **high**  
Operational density: **medium**

## Members

Brand expression: **low**  
Operational density: **medium–high**

## Requisitions

Brand expression: **moderate**  
Operational density: **high**

## Reports

Brand expression: **low**  
Operational density: **high**

## Authentication

Brand expression: **high**  
Operational density: **low**

## Audit Logs

Brand expression: **low**  
Operational density: **very high**

This keeps the product recognizably TCI without sacrificing the usability expected of a financial system.

---

# 42. Do's

- Use the TCI navy as the principal identity anchor.
- Use orange as the strongest action accent.
- Use green for positive/verified states.
- Use yellow for pending/attention states.
- Use light blue for information and secondary emphasis.
- Use Cabin consistently across operational UI.
- Use Libre Baskerville for selective branded/editorial moments.
- Keep dashboards predominantly white/light neutral.
- Use generous spacing and strong hierarchy.
- Use restrained borders instead of heavy shadows.
- Keep financial values visually prominent.
- Make workflow state obvious.
- Preserve the official logo proportions.
- Use the church statement selectively.

# 43. Don'ts

- Do not turn every component into a different brand color.
- Do not use yellow as normal body text.
- Do not use orange for every button.
- Do not use the logo as a decorative pattern throughout the application.
- Do not use heavy gradients or glassmorphism.
- Do not use excessive rounded pills for every container.
- Do not hide important financial information behind hover states.
- Do not use color alone to communicate status.
- Do not mix multiple unrelated font families.
- Do not make the financial application look like a promotional church website.
- Do not expose contributor phone numbers on receipts.
- Do not weaken backend authorization because a control is hidden in the UI.

---

# 44. Final Visual Direction

The finished TCI Higher Life Center application should feel like:

> **A trusted church financial system with a warm ministry identity and the clarity of a modern professional operations platform.**

The visual hierarchy should read:

**Navy = Trust & identity**  
**Orange = Action & warmth**  
**Green = Success & growth**  
**Yellow = Attention & pending**  
**Blue = Information & support**  
**White/neutral = Clarity & workspace**

The result should be recognizably TCI at first glance while remaining disciplined enough for financial records, receipts, requisitions, notifications, reports, and audit trails.

---

## Source Basis

This design system was derived from:

1. The supplied Rotary Civic design specification, used as the structural reference for documenting colors, typography, spacing, components, layout, elevation, shapes, and design rules. fileciteturn9file0L133-L176
2. The supplied TCI Higher Life Center branding PDF, used for TCI's logo identity, color palette, typography direction, and church statement. fileciteturn9file1L1-L10

Where the TCI branding source did not provide formal digital token values or component rules, this document defines implementation-oriented values and rules as design-system recommendations rather than claiming they are official brand specifications.
