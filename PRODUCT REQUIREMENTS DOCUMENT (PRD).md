# **PRODUCT REQUIREMENTS DOCUMENT (PRD)**

## **TCI Higher Life Center Receipt & Financial Management System**

**Organization:** TCI Higher Life Center  
**Document Type:** Product Requirements Document  
**Version:** 1.0  
**Status:** Requirements Consolidated  
**Date:** September 2026

---

# **1\. Introduction**

## **1.1 Purpose**

The **TCI Higher Life Center Receipt & Financial Management System** is a church financial and administrative application designed to digitize the recording, processing, receipt generation, requisition management, disbursement tracking, notifications, reporting, and audit management of church financial activities.

The system is intended to replace manual processes with a controlled and traceable digital workflow while maintaining accurate historical records and appropriate access controls.

## **1.2 Problem Statement**

The church requires processes for handling contributions and financial requisitions in a manner that is:

* Accurate  
* Traceable  
* Secure  
* Easy to manage  
* Easy to report on  
* Resistant to duplicate or unauthorized transactions  
* Capable of preserving historical records

The system will provide a centralized platform for these activities.

## **1.3 Objectives**

The system should:

1. Automate receipt generation for confirmed digital contributions.  
2. Allow authorized users to record contributions received outside the integrated payment channel.  
3. Provide personalized contribution thank-you messages.  
4. Manage church members and contributor identification.  
5. Manage requisitions from request through disbursement and proof verification.  
6. Provide permission-based access to internal users.  
7. Preserve financial and operational history through audit trails.  
8. Provide useful financial and administrative reports.  
9. Detect and appropriately handle payment, receipt, and notification exceptions.  
10. Provide a foundation that can support future integrations and communication channels.

---

# **2\. Scope**

## **2.1 In Scope**

Version 1 includes:

* Authentication and user management  
* Role/permission management  
* Member and contributor management  
* Automatic digital contribution processing  
* Manual contribution recording  
* Receipt generation and management  
* Personalized thank-you messages  
* SMS notifications  
* Requisition management  
* Disbursement tracking  
* Proof-of-expenditure submission and verification  
* Reports  
* Audit trails  
* Exception and manual-action management  
* Secure receipt and evidence links

## **2.2 Currently Out of Scope**

The following are not required for Version 1:

* WhatsApp notifications  
* Contributor/member accounts  
* Technology-stack implementation decisions  
* A specific payment provider as a permanent requirement  
* A specific SMS provider as a permanent requirement  
* Advanced approval-threshold rules that have not yet been defined

WhatsApp may be considered as a future enhancement.

---

# **3\. User Types and Access Model**

## **3.1 System Administrator**

The **System Administrator** is the highest-level application user.

Responsibilities include:

* Creating System Users  
* Deactivating System Users  
* Assigning and modifying permissions  
* Reviewing system-wide records  
* Reviewing reports  
* Reviewing audit trails  
* Reviewing deleted records  
* Managing system-level exceptions  
* Performing authorized reassignment or administrative recovery functions

A System User cannot create a System Administrator.

There is one System Administrator role at the highest level of the system.

## **3.2 System User**

A **System User** is an internal church user whose access is determined by assigned permissions.

A System User may have permissions such as:

* Create contribution  
* Generate receipt  
* Edit own manual receipts  
* Delete own manual receipts  
* Manage members  
* Review requisitions  
* Approve requisitions  
* Process disbursements  
* Verify evidence  
* Export reports

Permissions must be configurable and not hardcoded to individual people.

For example, one System User may be given permission to manage users while another may be given permission to approve requisitions.

## **3.3 External Requester**

A requester uses the separate requisition portal to submit and track requisitions.

A requester does **not** need an internal System User account.

A requester may be:

* Individual church member  
* Ministry  
* Team  
* Group  
* Department

---

# **4\. Authentication and User Management**

## **4.1 Login**

System Users shall authenticate using registered credentials.

On successful login, the system shall:

* Verify credentials  
* Verify account status  
* Establish a secure session  
* Apply the user's permissions  
* Record login activity

## **4.2 Password Recovery**

Every System User must have a working email address.

Password recovery shall support:

* Secure reset link or OTP  
* Expiration  
* Single-use reset credentials  
* Secure password storage  
* Audit logging of recovery activity

Passwords and reset credentials must never be stored in plaintext.

## **4.3 System Administrator Recovery**

A designated recovery email and recovery phone shall be used for System Administrator account recovery.

Recovery information must be stored as protected configuration/database values rather than hardcoded in application source code.

A stronger multi-factor recovery process should be used.

Changing recovery information requires an authorized controlled process and must be audited.

## **4.4 Session Security**

The system shall support:

* Automatic logout after configurable inactivity  
* Secure session/token handling  
* Protection against repeated failed login attempts  
* Ability to terminate active sessions where appropriate

## **4.5 User Deactivation**

A deactivated System User:

* Cannot log in  
* Retains historical activity records  
* Does not cause historical receipts or transactions to be deleted  
* Remains visible in relevant audit records

---

# **5\. Member and Contributor Management**

## **5.1 Member Record**

A member record shall contain:

* System-generated Member ID  
* First Name  
* Middle Name, optional  
* Last Name  
* Email Address, optional  
* Multiple phone numbers  
* Primary phone number, where applicable  
* Ministry/Team/Department, optional  
* Member Status  
* Date Added  
* Last Updated

The Member ID is the permanent internal identity of the member.

## **5.2 Multiple Phone Numbers**

A member may have multiple phone numbers.

Each phone number shall:

* Be stored independently  
* Be linked to the Member ID  
* Optionally be marked as primary  
* Be checked for conflicts before assignment

A phone number must not silently belong to multiple members.

## **5.3 Human Member Search**

Authorized System Users may search for members using:

* Member ID  
* Name  
* Phone number  
* Email address

## **5.4 Automatic Contributor Identification**

Automatic digital contributions shall identify existing members **strictly by transaction phone number**.

The system shall not automatically use:

* Provider account name  
* Name similarity  
* Email address  
* Provider identity

to override phone-number identification.

## **5.5 Temporary Contributor**

Where the transaction phone number is not found:

1. Create a temporary/unverified contributor record.  
2. Store available provider-supplied information.  
3. Continue processing the successful contribution.  
4. Generate the receipt.  
5. Make the record available for authorized review.

The temporary record may later be:

* Linked to an existing member through an authorized process, or  
* Converted into a permanent member.

## **5.6 Member Linking and Merging**

Member linking or merging requires **System Administrator approval**.

The process shall:

* Preserve historical contributions  
* Preserve relevant records  
* Record the reason  
* Record the requesting user  
* Record approval/rejection  
* Maintain an audit trail

## **5.7 Phone Conflict**

When a phone number already belongs to another member, the system shall flag the conflict and require authorized resolution.

## **5.8 Member Deactivation**

Members may be deactivated without deleting historical financial records.

---

# **6\. Contributions and Payments**

## **6.1 Contribution Types**

The system shall support:

1. Tithe  
2. Thanksgiving  
3. Higher Life Partners  
4. Building Project  
5. Other

When **Other** is selected during the automatic USSD process, the contributor shall enter the contribution type/description.

## **6.2 Contribution Identification**

Every contribution shall have a unique internal Contribution ID.

Example:

**CON-00001245**

The Contribution ID is separate from the receipt number.

## **6.3 Payment Modes**

The system shall support:

* Cash  
* Mobile Money  
* Cheque  
* Bank Transaction  
* Other

**Bank Transaction** must be explicitly labelled as such.

## **6.4 Entry Method**

The system shall distinguish:

* Automatic  
* Manual

### **Automatic**

Example:

> Church USSD/payment gateway → automatic Mobile Money contribution

### **Manual**

Examples:

* Cash  
* Direct Mobile Money  
* Cheque  
* Bank Transaction

## **6.5 Automatic Contribution Flow**

The automatic process shall follow:

Contributor  
    ↓  
Church USSD / Payment Gateway  
    ↓  
"Welcome to TCI HLC Givings"  
    ↓  
Select Contribution Type  
    ↓  
Enter Amount  
    ↓  
Complete Payment  
    ↓  
Provider Confirmation  
    ↓  
Identify Contributor by Transaction Phone Number  
    ↓  
Record Contribution  
    ↓  
Generate Receipt  
    ↓  
Send Personalized Thank-You  
    ↓  
Send Receipt SMS with Secure PDF Link

## **6.6 Manual Contribution Flow**

The manual process shall follow:

Contributor  
    ↓  
System User Records Contribution  
    ↓  
Find Existing Member or Create Member  
    ↓  
Select Contribution Type  
    ↓  
Select Payment Mode  
    ↓  
Enter Amount and Relevant Reference  
    ↓  
Save Contribution  
    ↓  
Generate Receipt  
    ↓  
Send Personalized Thank-You Message  
    ↓  
User Downloads Receipt as PDF

## **6.7 Payment Reference**

Automatic transactions shall retain the provider reference number.

Manual transactions may retain relevant references such as:

* Bank transaction reference  
* Mobile Money transaction reference  
* Cheque number  
* Other appropriate reference

---

# **7\. Payment Exception Handling**

## **7.1 Failed Payment**

For failed payments:

* No successful contribution is created  
* No receipt is generated  
* No thank-you message is sent  
* Provider failure code/reason is recorded where available  
* Technical information is retained for audit/review  
* An appropriate user-facing failure message may be sent

Failure categories may include:

* Insufficient funds  
* Invalid transaction  
* Provider/service error  
* Network/timeout  
* Account restriction  
* Transaction limit  
* Unknown

## **7.2 Cancelled Payment**

For cancelled transactions:

* No successful contribution  
* No receipt  
* No thank-you message

Cancellation information may remain in transaction/provider history.

## **7.3 Delayed Confirmation**

When payment confirmation is delayed:

* Transaction remains pending  
* No receipt is generated  
* No thank-you message is sent  
* Reconciliation/retry processes continue  
* Normal processing begins when confirmation is received

## **7.4 Duplicate Confirmation**

Duplicate detection shall use:

**Provider Reference Number \+ Contribution Type**

For an exact duplicate:

* Do not create another contribution  
* Do not create another receipt  
* Log the duplicate  
* Notify authorized users where appropriate

If the same provider reference appears with a different contribution type, the transaction shall be flagged for review rather than automatically discarded.

## **7.5 Merchant Number Transaction**

When a transaction originates from a merchant number:

* Do not automatically generate a receipt  
* Log the transaction  
* Notify an authorized System User  
* Create a manual review/handling requirement

---

# **8\. Receipt Management**

## **8.1 Receipt Number**

Every receipt shall use the format:

**HLC-RANDOMIZED 7 DIGITS**

Example:

**HLC-5831047**

Receipt numbers must:

* Be unique  
* Never be reused  
* Remain associated with their historical record  
* Not be reused after deletion

## **8.2 Receipt Contents**

A receipt shall contain:

* TCI Higher Life Center  
* Church logo  
* Receipt number  
* Contributor name  
* Amount  
* Contribution type  
* Payment mode  
* Date  
* Relevant transaction/reference information where appropriate  
* Generated By

The contributor's phone number shall **not** appear on the receipt.

## **8.3 Generated By**

### **Manual Receipt**

**Generated By: \[System User\]**

### **Automatic Receipt**

**Generated By: HLC/System**

## **8.4 Manual Receipt Editing**

A System User may edit a manual receipt they created, subject to permission.

Users cannot edit another ordinary user's receipt.

Every edit shall record:

* Receipt number  
* Contribution ID  
* User  
* Date/time  
* Changed field  
* Previous value  
* New value  
* Reason/remarks where required

## **8.5 Automatic Receipt Immutability**

Automatic receipts are finalized and immutable.

They shall not be edited through the normal application workflow.

## **8.6 Manual Receipt Deletion**

A System User may delete their own manual receipt subject to permission.

Deletion requires confirmation.

The system shall use soft deletion.

Deleted records:

* Disappear from the normal user view  
* Remain available to the System Administrator  
* Remain in the audit history  
* Retain their original receipt number

## **8.7 Receipt Storage**

The system shall securely store generated receipt PDFs.

The relationship shall be:

**Contribution ID → Receipt Number → Receipt PDF**

## **8.8 Receipt Delivery**

Automatic receipts shall be delivered through SMS as a text-format receipt plus a secure link to the PDF.

The SMS shall contain information such as:

* Church name  
* Receipt number  
* Contribution type  
* Amount  
* Date  
* Thank-you message  
* Secure receipt link

The PDF itself is **not** sent as an SMS attachment.

Manual receipts shall be available to the System User for preview and PDF download.

## **8.9 Receipt Verification**

The system shall support receipt verification using the unique receipt number.

The verification response shall expose only appropriate information and must not reveal unnecessary personal data.

A QR code may be considered as a future enhancement.

---

# **9\. Thank-You Messaging**

## **9.1 General Rule**

Both automatic and manual successful contributions shall trigger a personalized thank-you message.

The thank-you message is separate from receipt delivery.

## **9.2 Personalization**

The message shall use the contributor's first name where available.

For unmatched automatic contributors, available provider-supplied first-name information may be used.

## **9.3 Contribution-Specific Templates**

Messages shall be tailored to the contribution type:

* Tithe  
* Thanksgiving  
* Building Project  
* Higher Life Partners  
* Other

The system should use configurable templates rather than requiring code changes whenever wording needs to be updated.

Templates may contain variables such as:

* `{first_name}`  
* `{amount}`  
* `{contribution_type}`  
* `{receipt_number}`  
* `{date}`  
* `{secure_link}`

---

# **10\. Notifications**

## **10.1 Notification Channel**

**SMS is the required notification channel for Version 1\.**

WhatsApp is outside the initial scope.

## **10.2 Notification Types**

The system shall support notifications for:

* Successful contribution thank-you  
* Automatic receipt  
* Payment failure  
* Requisition approval  
* Requisition rejection  
* Funds ready for disbursement  
* Post-disbursement proof upload  
* Evidence rejection  
* Failed receipt generation  
* Failed notifications  
* Merchant transactions  
* Duplicate transaction alerts  
* Other authorized system events

## **10.3 Notification Lifecycle**

Notifications shall follow:

**Pending → Sending → Sent → Delivered**

Failures shall follow:

**Pending → Sending → Failed → Retrying → Manual Action Required**

## **10.4 Retry Policy**

### **Thank-You Messages**

Maximum:

**3 attempts**

### **Receipt Generation**

Maximum:

**3 attempts**

After the third failure:

* Log the failure  
* Create a Manual Action item  
* Notify an appropriate authorized System User

The same retry framework may be applied to other notification processes unless separately configured.

## **10.5 Notification Independence**

A notification failure shall never reverse the underlying financial or administrative event.

For example:

> Successful payment \+ failed SMS \= successful contribution.

> Approved requisition \+ failed SMS \= approved requisition.

## **10.6 Notification History**

Each notification shall retain:

* Notification ID  
* Notification type  
* Recipient  
* Related record  
* Channel  
* Status  
* Attempt count  
* Date/time  
* Provider reference where available  
* Failure reason where available

---

# **11\. Manual Action Management**

The system shall create manual-action records for issues that cannot be completed automatically.

Examples:

* Receipt generation failed after 3 attempts  
* Thank-you message failed after 3 attempts  
* Merchant-number transaction requires review  
* Other configured exceptions requiring human intervention

A Manual Action item should contain:

* Related record  
* Issue type  
* Description  
* Attempts  
* Current status  
* Date/time  
* Responsible/authorized user  
* Resolution details

---

# **12\. Requisition Management**

## **12.1 Requesters**

Requests may be submitted by:

* Individual church member  
* Ministry  
* Team  
* Group  
* Department

## **12.2 Requisition ID**

Every requisition shall have a unique permanent identifier.

Example:

**REQ-000125**

## **12.3 Requisition Form**

The requester shall provide:

* Requester name  
* Requester phone number  
* Ministry/team/department/group, where applicable  
* Purpose  
* Detailed description  
* Amount requested  
* Date funds are needed  
* Preferred disbursement method  
* Relevant disbursement details  
* Supporting documents, where applicable  
* Remarks/priority, where applicable

## **12.4 Preferred Disbursement Method**

The requester may specify:

* Cash  
* Mobile Money  
* Bank Transfer  
* Other

The preferred method is separate from the actual method used.

## **12.5 Drafts**

A requester may:

* Create a draft  
* Save the draft  
* Return later  
* Submit the requisition

## **12.6 Requisition Lifecycle**

Recommended system statuses:

* Draft  
* Submitted  
* Pending Review  
* Under Review  
* Approved  
* Rejected  
* Pending Disbursement  
* Disbursed  
* Evidence Submitted  
* Under Verification  
* Completed/Closed

---

# **13\. Requisition Assignment**

## **13.1 Automatic Assignment**

Authorized System Users may view pending requisitions according to their permissions.

When an authorized user selects **Start Working**:

* The system automatically assigns the requisition to that user.  
* Assignment date/time is recorded.  
* The user becomes responsible for progressing the request.

There is no normal manual self-assignment workflow.

## **13.2 Assignment Lock**

Once assigned:

* Other users may see who is handling the requisition.  
* Other users cannot open, edit, process, approve, or reject that requisition while it is locked to the assigned user.

## **13.3 Reassignment**

An appropriately authorized System Administrator or authorized user may reassign/unlock the requisition when necessary.

Reassignment shall:

* Record previous handler  
* Record new handler  
* Record date/time  
* Record reason  
* Preserve the complete assignment history

---

# **14\. Requisition Review, Approval and Rejection**

## **14.1 Review**

The assigned user reviews:

* Requester details  
* Purpose  
* Description  
* Requested amount  
* Supporting documents  
* Preferred disbursement method  
* Other relevant information

## **14.2 Approval**

Approval requires the appropriate permission.

The system records:

* Approved amount  
* Approver  
* Date/time  
* Remarks

Approval changes the status to:

**Pending Disbursement**

Approval triggers an SMS to the requester.

## **14.3 Rejection**

When rejected:

* Rejection reason is required  
* Approver identity is recorded  
* Date/time is recorded  
* Status becomes Rejected  
* Rejection history is permanently preserved  
* Requester receives an SMS

A rejected requisition **cannot be edited or resubmitted**.

If the requester still requires funds, they must create a **new requisition** with a new Requisition ID.

A new requisition may optionally reference the previous requisition for historical context.

---

# **15\. Requisition Amount Tracking**

The following must remain separate:

**Requested Amount**

**Approved Amount**

**Disbursed Amount**

Example:

Requested:  GHS 5,000  
Approved:   GHS 4,000  
Disbursed:  GHS 3,500

All three values must remain in the historical record.

---

# **16\. Disbursement Management**

## **16.1 Actual Disbursement**

When funds are released, the system shall record:

* Approved amount  
* Actual amount disbursed  
* Actual disbursement method  
* Recipient  
* Date/time  
* Processing System User  
* Transaction/reference number  
* Remarks  
* Supporting acknowledgement/proof where applicable

## **16.2 Actual Methods**

The system shall support:

* Cash  
* Mobile Money  
* Bank Transfer  
* Other

## **16.3 Cash Disbursement**

Cash disbursement should record:

* Recipient  
* Amount  
* Date/time  
* Processing user  
* Acknowledgement/signature where church policy requires

## **16.4 Electronic Disbursement**

Mobile Money and bank disbursements should retain:

* Recipient  
* Amount  
* Date/time  
* Method  
* Transaction/reference number  
* Processing System User

---

# **17\. Proof of Expenditure**

## **17.1 Open Status After Disbursement**

A requisition does not become complete merely because funds have been disbursed.

The status becomes:

**Disbursed → Evidence Required**

The requester receives an SMS containing a secure upload link.

## **17.2 Evidence Upload**

The link shall identify the relevant requisition automatically so that the requester does not need to manually enter the requisition ID.

Supported evidence formats may include:

* PDF  
* JPG  
* PNG  
* TXT  
* Other approved formats

## **17.3 Evidence Record**

The system records:

* Requisition ID  
* Requester  
* File  
* File type  
* Upload date/time  
* Verification status  
* Reviewer  
* Review date/time  
* Remarks

## **17.4 Evidence Verification**

Lifecycle:

**Evidence Submitted → Under Verification → Verified**

or:

**Evidence Submitted → Under Verification → Rejected**

If rejected:

* Reason must be recorded.  
* Requisition remains open.  
* Requester may upload corrected/additional evidence.  
* Previous evidence and rejection history remain preserved.

## **17.5 Closure**

A requisition may only be closed after:

Approved  
   ↓  
Disbursed  
   ↓  
Proof Submitted  
   ↓  
Proof Verified  
   ↓  
Closed

The system must prevent premature closure.

---

# **18\. Reports and System Oversight**

## **18.1 System Administrator Dashboard**

The dashboard shall provide system-wide visibility into:

### **Contributions**

* Contributions received  
* Amount received  
* Automatic/manual contributions  
* Contributions by type  
* Contributions by payment mode

### **Receipts**

* Receipts generated  
* Manual/automatic receipts  
* Deleted receipts  
* Failed receipt generation  
* Delivery failures

### **Requisitions**

* Pending  
* Approved  
* Rejected  
* Disbursed  
* Awaiting evidence  
* Under verification  
* Completed

### **Exceptions**

* Failed payments  
* Delayed confirmations  
* Duplicate transactions  
* Failed notifications  
* Manual actions  
* Temporary contributors awaiting review

## **18.2 Contribution Reports**

Reports shall include:

* Contribution ID  
* Receipt number  
* Contributor/member  
* Contribution type  
* Amount  
* Payment mode  
* Entry method  
* Date  
* Reference number  
* Status  
* Generated by

## **18.3 Receipt Reports**

Reports shall include:

* Receipt number  
* Contribution ID  
* Contributor  
* Amount  
* Contribution type  
* Payment mode  
* Date  
* Entry method  
* Generated by  
* Receipt status

## **18.4 Requisition Reports**

Reports shall include:

* Requisition ID  
* Requester  
* Ministry/team/department/group  
* Purpose  
* Requested amount  
* Approved amount  
* Disbursed amount  
* Preferred disbursement method  
* Actual disbursement method  
* Assigned System User  
* Status  
* Relevant dates  
* Evidence status

## **18.5 Member Reports**

Reports may include:

* Member ID  
* Name  
* Email  
* Phone numbers  
* Primary phone  
* Ministry/team/department  
* Status  
* Date added  
* Last updated  
* Temporary contributors  
* Phone conflicts  
* Merge/link requests

## **18.6 User Activity Reports**

The System Administrator shall be able to review user activity such as:

* Login activity  
* Contribution activity  
* Receipt activity  
* Member activity  
* Requisition activity  
* Permission changes  
* Other auditable operations

---

# **19\. Audit Management**

## **19.1 Audit Trail**

Important system actions must be auditable.

The audit trail should answer:

> **Who did what, when, and what changed?**

Each audit record should contain, where applicable:

* User/system responsible  
* Action performed  
* Record affected  
* Date/time  
* Old value  
* New value  
* Reason/remarks  
* Result/status  
* Relevant provider/system reference

## **19.2 Auditable Actions**

### **User Management**

* User creation  
* User deactivation  
* Permission changes  
* Password recovery  
* Administrator recovery

### **Contributions and Receipts**

* Contribution creation  
* Receipt generation  
* Receipt editing  
* Receipt deletion  
* Automatic transaction receipt creation  
* Duplicate detection  
* Payment failure  
* Receipt failure  
* Delivery attempt

### **Members**

* Member creation  
* Phone changes  
* Temporary contributor review  
* Linking  
* Merge request  
* Merge approval/rejection  
* Phone conflict resolution  
* Member deactivation

### **Requisitions**

* Submission  
* Assignment  
* Reassignment  
* Approval  
* Rejection  
* Disbursement  
* Evidence upload  
* Evidence rejection  
* Evidence verification  
* Closure

### **Notifications**

* Creation  
* Sending  
* Delivery  
* Failure  
* Retry  
* Manual-action creation/resolution

## **19.3 Audit Integrity**

Audit records shall not be editable or deletable during normal operation.

Historical records must remain intact.

---

# **20\. Search, Filtering and Export**

## **20.1 Search**

Authorized users shall be able to search using relevant identifiers such as:

* Member ID  
* Contribution ID  
* Receipt number  
* Requisition ID  
* Transaction/reference number

## **20.2 Filtering**

Reports shall support filters such as:

* Date range  
* Member/contributor  
* Contribution type  
* Payment mode  
* Entry method  
* User  
* Requisition status  
* Ministry/team/department/group  
* Requester  
* Disbursement method  
* Evidence status  
* Notification status

## **20.3 Reporting Periods**

The system should support:

* Today  
* Yesterday  
* This week  
* This month  
* This year  
* Custom date range

## **20.4 Export**

Authorized users may export reports in:

* PDF  
* Excel/CSV

Exports must respect the requesting user's permissions.

---

# **21\. Record Preservation**

A core system principle is:

> **Important financial and operational records should be preserved rather than silently overwritten or physically erased.**

Examples:

* Deleted receipts retain historical records.  
* Rejected requisitions remain permanently recorded.  
* New requisitions receive new identifiers.  
* Member merges preserve contribution history.  
* Reassignments preserve assignment history.  
* Receipt edits preserve old and new values in the audit trail.  
* Deactivated users and members retain historical references.

---

# **22\. Security and Data Protection Requirements**

The system shall provide:

* Secure authentication  
* Password hashing  
* Secure password recovery  
* Session expiration  
* Failed-login protection  
* Secure tokens  
* Permission enforcement  
* Protected administrator recovery  
* Secure receipt links  
* Secure evidence-upload links  
* Input validation  
* Audit logging  
* Appropriate file validation  
* Protection against unauthorized access to financial information

Receipt and evidence links must not expose predictable internal file paths or unrestricted public storage.

---

# **23\. Core Business Rules**

The following rules are fundamental to Version 1:

1. Automatic contributor identification uses the **transaction phone number only**.  
2. Contributor phone numbers are **not displayed on receipts**.  
3. A member may have multiple phone numbers.  
4. Automatic receipts are immutable.  
5. Manual receipt editing is limited to the creator, subject to permission.  
6. Users cannot normally access or manage another ordinary user's receipts.  
7. Deleted receipts are soft-deleted and remain auditable.  
8. Receipt numbers are never reused.  
9. Contribution IDs are unique and permanent.  
10. Requisition IDs are unique and permanent.  
11. Requested, approved, and disbursed amounts remain separate.  
12. Preferred disbursement method and actual disbursement method remain separate.  
13. Approval does not equal disbursement.  
14. Disbursement does not equal requisition completion.  
15. Proof must be submitted and verified before requisition closure.  
16. Rejected requisitions cannot be edited or resubmitted.  
17. A new requisition must be created after rejection.  
18. Financial records are not reversed because an SMS or notification fails.  
19. Notification failures are retried and may create Manual Action items.  
20. SMS is the required notification channel for Version 1\.  
21. Audit records are preserved.  
22. Permissions are configurable and not hardcoded to named individuals.

---

# **24\. Major System Flows**

## **24.1 Automatic Contribution**

Contributor  
    ↓  
USSD / Payment Gateway  
    ↓  
Contribution Type  
    ↓  
Amount  
    ↓  
Payment  
    ↓  
Provider Confirmation  
    ↓  
Phone Number Matching  
    ↓  
Existing Member / Temporary Contributor  
    ↓  
Contribution Record  
    ↓  
Receipt Generation  
    ↓  
Personalized Thank-You SMS  
    ↓  
Receipt SMS \+ Secure PDF Link

## **24.2 Manual Contribution**

Contributor  
    ↓  
System User  
    ↓  
Find/Create Member  
    ↓  
Contribution Details  
    ↓  
Save  
    ↓  
Manual Receipt  
    ↓  
Personalized Thank-You SMS  
    ↓  
User Downloads PDF

## **24.3 Requisition**

Requester  
    ↓  
Create / Save Draft  
    ↓  
Submit  
    ↓  
Pending Review  
    ↓  
Authorized User Starts Working  
    ↓  
Automatic Assignment  
    ↓  
Review  
    ↓  
Approve / Reject

### **Approved Path**

Approved  
    ↓  
Pending Disbursement  
    ↓  
Funds Disbursed  
    ↓  
Requester Receives Upload Link  
    ↓  
Evidence Submitted  
    ↓  
Evidence Verification  
    ↓  
Verified  
    ↓  
Completed / Closed

### **Rejected Path**

Rejected  
    ↓  
Permanent Historical Record  
    ↓  
New Requisition Required

---

# **25\. Success Criteria**

Version 1 will be considered functionally successful when the system can:

* Securely authenticate authorized users.  
* Apply permission-based access.  
* Record manual contributions.  
* Process confirmed automatic contributions.  
* Identify automatic contributors by transaction phone number.  
* Handle unknown contributors.  
* Generate unique receipts.  
* Generate automatic and manual receipts correctly.  
* Trigger personalized thank-you messages for both automatic and manual contributions.  
* Deliver receipt information through SMS.  
* Provide secure receipt access.  
* Manage members and multiple phone numbers.  
* Detect and handle payment exceptions.  
* Manage requisitions from submission through closure.  
* Automatically assign requisitions when authorized users start working.  
* Track actual disbursements.  
* Collect and verify proof of expenditure.  
* Preserve rejected, deleted, and historical records.  
* Produce operational reports.  
* Maintain a reliable audit trail.  
* Surface exceptions requiring manual intervention.

---

# **26\. Requirements Still Intentionally Open**

The functional requirements are substantially defined. The following implementation decisions remain open and should be determined in the next project stage.

## **26.1 Payment Provider**

The exact provider/integration may be:

* MTN MoMo  
* Paystack  
* Another suitable supported provider

The provider is not a permanent Version 1 business requirement at this stage.

## **26.2 SMS Provider**

The exact SMS provider has not yet been selected.

## **26.3 Approval Rules**

Detailed approval thresholds or amount-based approval hierarchies have not yet been finalized.

## **26.4 Receipt Design**

The final visual receipt layout, typography, branding details, and exact field arrangement still need to be designed.

## **26.5 Technology Stack**

The application architecture, programming languages, frameworks, database, hosting, and deployment strategy were intentionally not selected in the requirements-validation document.

Technology selection is a subsequent project stage.

---

# **27\. Future Enhancements**

Potential future functionality includes:

* WhatsApp notifications  
* QR code on receipts  
* More advanced approval workflows  
* Additional payment providers  
* Additional notification channels  
* Expanded financial analytics  
* Additional integrations

These are not part of the current Version 1 scope.

---

# **28\. Overall Product Principles**

The system should be built around four principles.

### **Accuracy**

Financial transactions must be correctly recorded.

### **Traceability**

Important actions must be attributable to the responsible user or system process.

### **Security**

Financial and personal information must only be accessible to authorized parties.

### **Historical Integrity**

Important records must remain preserved so that the church can always understand what happened, who was involved, and when it happened.

---

# **29\. Final Product Definition**

The **TCI Higher Life Center Receipt & Financial Management System** is a centralized church financial and administrative platform that connects:

Members & Contributors  
        │  
        ├── Automatic Contributions  
        │       │  
        │       ├── Payment Processing  
        │       ├── Contributor Identification  
        │       ├── Receipt Generation  
        │       └── SMS / Thank-You  
        │  
        ├── Manual Contributions  
        │       │  
        │       ├── Member Selection  
        │       ├── Contribution Recording  
        │       ├── Receipt Generation  
        │       └── Thank-You  
        │  
        └── Member Management

                    ↓

             Financial Records  
                    │  
                    ├── Receipts  
                    ├── Notifications  
                    ├── Exceptions  
                    └── Audit Trail

                    ↑

             Requisition System  
                    │  
                    ├── Request  
                    ├── Review  
                    ├── Assignment  
                    ├── Approval / Rejection  
                    ├── Disbursement  
                    ├── Evidence Upload  
                    ├── Verification  
                    └── Closure

                    ↓

          Reports & System Oversight

The product's central requirement is that every important financial or administrative event must be **accurately recorded, traceable, secure, and historically preserved**.

