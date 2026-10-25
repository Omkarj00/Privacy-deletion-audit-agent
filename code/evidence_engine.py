"""Evidence Engine: Indexing and query engine for all evidence files."""
import csv, json, sqlite3, os, re
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Any
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"

def parse_iso(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

@dataclass
class EvidenceFileMeta:
    file_id: str
    file_path: str
    origin: str
    captured_at: str
    captured_dt: datetime

@dataclass
class EvidenceFinding:
    file_id: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    details: Dict[str, Any] = field(default_factory=dict)

class EvidenceEngine:
    def __init__(self, dataset_dir: Path = DATASET):
        self.dataset_dir = dataset_dir
        self.manifest: Dict[str, EvidenceFileMeta] = {}
        self.path_to_file_id: Dict[str, str] = {}
        self._load_manifest()

    def _load_manifest(self):
        with open(self.dataset_dir / "evidence_files.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fid = r["file_id"].strip()
                fpath = r["file_path"].strip()
                orig = r["origin"].strip()
                cap = r["captured_at"].strip()
                meta = EvidenceFileMeta(
                    file_id=fid,
                    file_path=fpath,
                    origin=orig,
                    captured_at=cap,
                    captured_dt=parse_iso(cap)
                )
                self.manifest[fid] = meta
                self.path_to_file_id[fpath] = fid
                # Also store normalized path
                norm_path = fpath.replace("\\", "/").strip("/")
                self.path_to_file_id[norm_path] = fid

    def get_eligible_evidence(self, audit_at: str) -> List[EvidenceFileMeta]:
        adt = parse_iso(audit_at)
        eligible = [e for e in self.manifest.values() if e.captured_dt <= adt]
        eligible.sort(key=lambda x: x.captured_dt, reverse=True)
        return eligible

    def search_service_resource_export(self, file_id: str, identifiers: Set[str]) -> List[EvidenceFinding]:
        meta = self.manifest.get(file_id)
        if not meta:
            return []
        path = self.dataset_dir / meta.file_path
        if not path.exists():
            return []
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        exports = data.get("service_exports", {})
        findings = []

        # 1. account_service
        acc_lookups = exports.get("account_service", {}).get("profile_lookups", [])
        for item in acc_lookups:
            rid = item.get("resource_id")
            prof = item.get("profile") or {}
            ref = prof.get("customer_reference")
            em = prof.get("email")
            ph = prof.get("phone")
            eh = prof.get("email_hash")
            matches = any(x in identifiers for x in [ref, em, ph, eh] if x)
            if matches:
                code = item.get("status_code", 404)
                if code == 200:
                    findings.append(EvidenceFinding(file_id, "account_service", rid, "REPORT_REMAINING_RECORD", "PRESENT", item))
                else:
                    findings.append(EvidenceFinding(file_id, "account_service", rid, "VERIFY_RECORD_ABSENT", "ABSENT", item))

        # 2. search_service
        search_queries = exports.get("search_service", {}).get("index_queries", [])
        for item in search_queries:
            rid = item.get("resource_id")
            hits = item.get("hits", {})
            total = hits.get("total", 0)
            docs = hits.get("documents", [])
            matches = False
            for doc in docs:
                if any(x in identifiers for x in [doc.get("customer_reference"), doc.get("email"), doc.get("phone"), doc.get("email_hash")] if x):
                    matches = True
                    break
            q = item.get("query")
            if q and any(x in q for x in identifiers):
                matches = True
            if matches:
                if total > 0:
                    findings.append(EvidenceFinding(file_id, "search_service", rid, "REPORT_REMAINING_RECORD", "PRESENT", item))
                else:
                    findings.append(EvidenceFinding(file_id, "search_service", rid, "VERIFY_RECORD_ABSENT", "ABSENT", item))

        # 3. export_service
        export_items = exports.get("export_service", {}).get("exports", [])
        for item in export_items:
            rid = item.get("resource_id")
            ref = item.get("customer_reference")
            em = item.get("owner_email")
            ph = item.get("owner_phone")
            if any(x in identifiers for x in [ref, em, ph] if x):
                findings.append(EvidenceFinding(file_id, "export_service", rid, "REPORT_REMAINING_RECORD", "PRESENT", item))

        # 4. analytics_platform
        analytics_profiles = exports.get("analytics_platform", {}).get("profiles", [])
        for item in analytics_profiles:
            rid = item.get("resource_id")
            ref = item.get("customer_reference")
            em = item.get("email")
            ph = item.get("phone")
            eh = item.get("email_hash")
            matches = any(x in identifiers for x in [ref, em, ph, eh] if x)
            if matches:
                if any([em, ph, eh]):
                    findings.append(EvidenceFinding(file_id, "analytics_platform", rid, "REPORT_EXPOSED_PII", "PRESENT", item))
                else:
                    findings.append(EvidenceFinding(file_id, "analytics_platform", rid, "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED", item))

        # 5. support_platform
        support_tickets = exports.get("support_platform", {}).get("tickets", [])
        for item in support_tickets:
            rid = item.get("resource_id")
            ref = item.get("customer_reference")
            em = item.get("requester_email")
            ph = item.get("requester_phone")
            nm = item.get("requester_name")
            if any(x in identifiers for x in [ref, em, ph, nm] if x):
                if any([em, ph, nm]):
                    findings.append(EvidenceFinding(file_id, "support_platform", rid, "REPORT_EXPOSED_PII", "PRESENT", item))
                else:
                    findings.append(EvidenceFinding(file_id, "support_platform", rid, "VERIFY_RECORD_REDACTED", "REDACTED", item))

        # 6. integration_service
        connectors = exports.get("integration_service", {}).get("connectors", [])
        for item in connectors:
            rid = item.get("resource_id")
            ref = item.get("customer_reference")
            em = item.get("customer_email")
            if any(x in identifiers for x in [ref, em] if x):
                enabled = item.get("enabled", False)
                if enabled:
                    findings.append(EvidenceFinding(file_id, "integration_service", rid, "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", item))
                else:
                    findings.append(EvidenceFinding(file_id, "integration_service", rid, "VERIFY_DATA_FLOW_DISABLED", "DISABLED", item))

        # 7. backup_service
        backups = exports.get("backup_service", {}).get("restore_entries", [])
        for item in backups:
            rid = item.get("resource_id")
            ref = item.get("customer_reference")
            em = item.get("customer_email")
            if any(x in identifiers for x in [ref, em] if x):
                allowed = item.get("restore_allowed", False)
                if allowed:
                    findings.append(EvidenceFinding(file_id, "backup_service", rid, "REPORT_RESTORE_RISK", "ACTIVE", item))
                else:
                    findings.append(EvidenceFinding(file_id, "backup_service", rid, "VERIFY_RESTORE_BLOCKED", "BLOCKED", item))

        # 8. security_service
        audits = exports.get("security_service", {}).get("audit_records", [])
        for item in audits:
            rid = item.get("resource_id")
            ref = item.get("subject_reference")
            if ref in identifiers:
                findings.append(EvidenceFinding(file_id, "security_service", rid, "VERIFY_RECORD_PRESENT", "PRESENT", item))

        return findings
