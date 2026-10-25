# Phase 0 Recon: Data Notes & Architecture Mapping

HackerRank Orchestrate (October 2026) — "Ghost Data Still Exists"
Repository: `hackerrank-orchestrate-october26-main`
Audit Dataset Analysis for Gemini Flash Free Tier Deployment.

---

## 1. Executive Summary & File Inventory

The dataset models an enterprise e-commerce platform ("Northstar Shop") undergoing GDPR / CCPA right-to-be-forgotten deletion audits across disparate microservices, replication pipelines, search indices, audit ledgers, exports, and backup archives.

### Primary Input Files
| File | Rows | Schema / Key Fields | Purpose |
|---|---|---|---|
| `dataset/deletion_requests.csv` | 518 | `case_id,email,requested_at,reported_status,reported_at,audit_at,request_text` | All 518 audit target cases. Exactly one prediction row per case required in `output.csv`. |
| `dataset/sample_deletion_requests.csv` | 32 | Same as above | 32 public sample cases with ground-truth outputs. |
| `dataset/sample_output.csv` | 32 | `case_id,deletion_status,steps_json` | Canonical ground-truth answers for the 32 sample cases. Format reference. |
| `dataset/user_identities.csv` | 851 | `user_id,name,email,phone,email_hash,updated_at` | Current authoritative identity snapshot. Stable `user_id` anchor. |
| `dataset/user_audit_log.csv` | 307 | `audit_id,user_id,event_time,field_name,old_value,new_value` | Historical identity mutations (107 email, 107 email_hash, 92 phone). |
| `dataset/system_activity.csv` | 4,012 | `activity_id,event_time,event_type,source_system,target_system,resource_id,reported_result,details` | Global log stream of operations (claims, not proof). |
| `dataset/deletion_rules.csv` | 15 rules | `rule_id,data_category,condition,required_action` | Policy rules governing data categories and actions (`DELETE`, `REDACT`, `ANONYMIZE`, `RETAIN`). |
| `dataset/evidence_files.csv` | 94 | `file_id,file_path,origin,captured_at` | Manifest of all physical evidence files in `dataset/evidence/`. |

---

## 2. Evidence Types & Normalized Evidence Schema

There are 94 evidence files in `dataset/evidence/` cataloged in `evidence_files.csv` (`evidence_01` to `evidence_94`).

### 2.1 SQL Snapshots (`dataset/evidence/databases/` — 24 files)
All files are SQLite-compatible SQL transaction exports (`BEGIN TRANSACTION; ... COMMIT;`).
- **File Categories**:
  1. `customer_data_inventory_YYYYMMDD.sql` (11 files): Comprehensive periodic database inventory. Tables include:
     - `snapshot_scope` (`origin_system`, `captured_at`)
     - `customer_profiles` (`resource_id`, `customer_reference`, `full_name`, `email`, `phone`, `email_hash`, `account_status`, `updated_at`)
     - `customer_search_documents` (`resource_id`, `customer_reference`, `display_name`, `email`, `phone`, `email_hash`, `index_name`, `updated_at`)
     - `customer_analytics_profiles` (`resource_id`, `customer_reference`, `email`, `phone`, `email_hash`, `audience_cohort`, `updated_at`)
     - `support_records` (`resource_id`, `customer_reference`, `requester_name`, `requester_email`, `requester_phone`, `message_excerpt`, `updated_at`)
     - `customer_exports` (`resource_id`, `customer_reference`, `owner_email`, `owner_phone`, `object_key`, `updated_at`)
     - `connector_subscriptions` (`resource_id`, `customer_reference`, `customer_email`, `connector_name`, `enabled`, `last_delivery_status`, `updated_at`)
     - `backup_restore_entries` (`resource_id`, `customer_reference`, `customer_email`, `archive_key`, `restore_allowed`, `updated_at`)
     - `security_audit_records` (`resource_id`, `subject_reference`, `event_code`, `retention_until`, `updated_at`)
  2. `customer_data_read_replica_YYYYMMDD.sql` (5 files) & `customer_data_reporting_replica_YYYYMMDD.sql` (2 files):
     - Compare `customer_data_primary` vs `customer_data_read_replica` or `customer_data_reporting_replica`.
     - Schema: `resource_id, origin_system, collection_name, record_json, updated_at`.
     - Replication drift: A record deleted from `customer_data_primary` may still exist in the read/reporting replica!
  3. `customer_data_regional_replicas_YYYYMMDD.sql` (3 files):
     - Tables: `customer_data_primary`, `customer_data_replica_ap_south`, `customer_data_replica_eu_west`.
  4. `customer_accounts_failover_replica_YYYYMMDD.sql` (2 files):
     - Tables: `customer_profiles_failover_replica`, `customer_profiles_primary`.
     - Direct proof of account profiles lingering in failover nodes (e.g. `account_005` in `evidence_54` for `case_05`).
  5. `customer_search_primary_YYYYMMDD.sql` (1 file):
     - Table: `search_documents_primary` (`resource_id`, `display_name`, `email`, `source_resource_id`, `indexed_at`).
- **How State is Read**: Run directly in SQLite in-memory database. Query each table by user identifier (`user_id`, `email`, `email_hash`, `phone`, `resource_id`). If found in any copy, the resource is `PRESENT`.

### 2.2 JSON Structured Records (`dataset/evidence/records/` — 28 files)
- **File Categories**:
  1. `service_resource_export_*.json` (11 files): Periodic exports grouping resources across 8 services:
     `account_service`, `analytics_platform`, `backup_service`, `export_service`, `integration_service`, `search_service`, `security_service`, `support_platform`.
     - Field contents: `resource_id`, `customer_reference`, `email`, `phone`, `observed_at`, `enabled`, `restore_allowed`, `audience_cohort`, `retention_until`.
  2. `customer_data_snapshot_bundle_*.json` (12 files): Snapshot bundles containing collections of data store items.
  3. `connector_configurations_*.json` (1 file): Connectors/data flows showing `enabled: true/false`, `last_delivery_status: stopped/delivered`.
  4. `customer_export_jobs_*.json` & `customer_export_manifest_*.json` (2 files): Active and completed user export jobs (`object_key`, `owner_email`).
  5. `customer_analytics_report_*.json` (1 file): Cohort and profile attribution.
  6. `security_audit_ledger_*.json` (1 file): Audit ledger showing `subject_reference`, `event_code`, `retention_until`.
- **How State is Read**: Deterministic JSON parser extracting records into the unified record schema.

### 2.3 Handover Text (`dataset/evidence/records/customer_account_handover_20261001.txt` — 1 file)
- **Format**: Pipe-delimited plain text shift handover table (`evidence_03`):
  `timestamp | system | resource_id | category | state | subject_reference`
  e.g.: `2026-10-01T12:00:00Z | account_service | account_4248 | USER_PROFILE | NOT_FOUND | novak.tomasz@icloud.com`
- **How State is Read**: Simple regex/split line parser. Zero model required.

### 2.4 Server & Observability Logs (`dataset/evidence/logs/` — 26 files)
- **Format**: Key-value delimited and syslog traces:
  `timestamp level=... service=... resource_id=... lookup_key=... marker_found=...`
- Key trace files:
  - `resource_lookup_trace_*.log` (11 files): Service readback logs proving presence/absence of accounts and identifiers.
  - `support_replica.log` (1 file, `evidence_61`): Proves missing deletion markers on replica databases (e.g. `nadia.ionescu@yahoo.com` -> `marker_found=false`).
  - `cloud_runtime_export_*.log`, `syslog_archive_*.log`, `structured_service_events_*.log`: Audit logs of operations and delivery events.
- **How State is Read**: Regex key=value parser extracting timestamp, service, resource_id, lookup_key, result, marker_found.

### 2.5 Images (`dataset/evidence/images/` — 11 PNG files)
Internal UI screenshots of operator portals:
1. `account_admin_console_20260926.png` (`evidence_04`)
2. `search_index_inspector_20260901.png` (`evidence_09`)
3. `connector_management_console_20260807.png` (`evidence_14`)
4. `analytics_profile_view_20260713.png` (`evidence_19`)
5. `customer_export_console_20260618.png` (`evidence_23`)
6. `support_ticket_console_20260524.png` (`evidence_27`)
7. `backup_restore_console_20260429.png` (`evidence_31`)
8. `deleted_account_reference_20260310.png` (`evidence_37`)
9. `object_storage_browser_20260213.png` (`evidence_45`)
10. `security_audit_explorer_20260905.png` (`evidence_47`)
11. `marketing_profile_console.png` (`evidence_60`)
- **Extraction Mechanism**: Multimodal vision via Gemini Flash `VISION_MODEL` (with local OCR fallback where available). One single batch-processed call per file, structured schema output, strictly cached to disk.

### 2.6 Documents (`dataset/evidence/documents/` — 4 PDF files)
Business documents:
1. `account_history_report_20260718.pdf` (`evidence_18`, 718 chars text)
2. `delivery_correction_attachment.pdf` (`evidence_58`, 837 chars text)
3. `legal_retention_register_20260529.pdf` (`evidence_35`, 1,002 chars text)
4. `security_retention_extract.pdf` (`evidence_55`, 616 chars text)
- **Extraction Mechanism**: All 4 PDFs contain 100% digital selectable text streams readable via `pypdf`/`fitz` (PyMuPDF) with zero OCR loss. Can be parsed deterministically with zero model calls or passed to Gemini if vision multimodal verification is required.

---

## 3. Quota-Driven Model Strategy

### 3.1 Model Roles & Environment Configuration
- `VISION_MODEL`: Multimodal Flash-class model (default `gemini-2.5-flash` / `gemini-1.5-flash` / `gemini-2.0-flash`) for the 11 PNG files.
- `BULK_MODEL`: Flash-Lite-class model (default `gemini-2.5-flash-lite` / `gemini-1.5-flash-lite` / `gemini-2.0-flash-lite`) for ambiguous intent verification.

### 3.2 Exact Call Budget
| Target | Estimated Files / Calls | Policy |
|---|---|---|
| PNG Console Images | Exactly 11 calls (1 per file) | Extracted once, cached as JSON facts on disk. |
| PDF Documents | 0 calls (direct PyMuPDF text) or 4 calls | Deterministic text extraction preferred; 0 calls needed. |
| Intent Batch Verification | 0 to 1 call | 10 non-deletion cases are deterministically detected and verified. |
| Retries / Cache misses | <= 5 calls reserved | Exponential backoff on 429. |
| **Total Call Budget** | **~11 - 16 calls total** | Well under the strict 20 calls/day free-tier ceiling! |

---

## 4. Identity Graph & Identifier Mapping

### 4.1 Request Email Resolution
- Total Requests in `deletion_requests.csv`: 518.
- Matches in `user_identities.csv` (current email): **417 cases** (80.5%).
- Matches in `user_audit_log.csv` (historical email): **101 cases** (19.5%):
  - 100 cases match `old_value` of historical email updates.
  - 1 case matches `new_value` of historical audit log.
- Unmatched emails: **0 cases** (100% coverage across dataset).

### 4.2 Rewinding Identifiers & Validity Windows
- The identity graph must resolve:
  1. Stable `user_id`
  2. Current Name, Email, Phone, Email Hash (SHA-256)
  3. Historical Emails, Phones, and Hashes from `user_audit_log.csv`
  4. Time-bounded validity: changes occurring after `audit_at` must not rewrite prior identifiers for that audit window.
  5. Case normalization: e.g. uppercase emails like `CHOI.JIHOON94@GMAIL.COM` must be lowercased for index lookups.

---

## 5. Temporal Validity & As-Of Selector

### 5.1 Hard Boundary (`audit_at`)
- Each request row specifies `audit_at` (e.g. `2026-09-03T11:30:00Z`).
- Invariant: **Never use or cite evidence captured after `audit_at`** (`captured_at <= audit_at`).
- Captures at exactly `captured_at == audit_at` are valid.
- Any file or event with `timestamp > audit_at` is invisible to the decision engine for that case.

### 5.2 Latest Capture Rule (Hypothesis Confirmed)
- When a resource appears in multiple historical snapshots:
  - The evidence cited must be the **latest valid capture** (`captured_at <= audit_at`) that proves the state of that resource.
  - In `sample_output.csv`:
    - `case_01` (audit_at: `2026-09-03T11:30:00Z`): cites `evidence_54` (captured `2026-09-02T23:00:00Z`).
    - `case_03` (audit_at: `2026-09-26T11:00:00Z`): cites `evidence_94` (captured `2026-09-26T10:38:56Z`).

---

## 6. Intent Classification & The 10 Non-Deletion Cases

### 6.1 Intent Gate Architecture
1. **Tier 1**: Deterministic multilingual rule engine + negation filter.
   - Deletion keywords across 8 languages: English, Spanish, French, Portuguese, German, Italian, Japanese, Korean, Arabic.
   - Non-deletion actions:
     - Access requests (DSAR / "copy of invoices" / "what data you hold")
     - Rectification / Correction ("surname misspelled", "replace mobile number")
     - Data Portability / Export ("download my account info in portable format")
     - Marketing Opt-Out ("remove me from marketing lists only", "stop promotional emails")
     - Account Support / Login Reset ("need help resetting login, do not close")
2. **Corroboration from `system_activity.csv`**:
   - There are exactly **10 `CLASSIFY_REQUEST` events** in `system_activity.csv` (e.g. `activity_3486`: "Privacy intake classified request for account_2461 as non-deletion; no destructive action was authorized").
   - These 10 events correspond **1-to-1** with `case_533` through `case_542`.
3. **Intent Breakdown across all 518 requests**:
   - **508 requests** authorize deletion -> evaluated as `COMPLETE` or `INCOMPLETE`.
   - **Exactly 10 requests** (`case_533` - `case_542`) do NOT authorize deletion -> classified as `NOT_APPLICABLE` with `steps_json=[]`.
   - Ambiguous requests needing Tier 2 model check: **0 requests** ambiguous once Tier 1 rules and activity corroboration run. (Optional sanity batch of 1 call for 10 cases).

---

## 7. Decoys, Claims vs Proof, and Resource Linking

### 7.1 Decoys and Similar Identifiers
- Example in `case_28`: "A search result with a similar email belongs to someone else, so please do not treat that record as mine."
- Grounded linking rule: A resource belongs to a case **only if** it matches:
  - Exact `user_id`
  - Exact current or historical email
  - Exact current or historical phone
  - Exact SHA-256 email hash
  - Direct `LINK_RESOURCE_IDENTITY` event from `system_activity.csv`
- Records matching partial substrings or unrelated users are ignored as decoys.

### 7.2 Claims vs Proof
- `reported_status` in `deletion_requests.csv` and `reported_result` in `system_activity.csv` are **claims**.
- Example: `case_05` has `reported_status=COMPLETE`, but `evidence_54` proves `account_005` is still `ACTIVE` in failover replica -> true status is `INCOMPLETE`.
- True state is reconstructed strictly from physical evidence files at or before `audit_at`.
