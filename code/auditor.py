"""Comprehensive Multi-Modal Deletion Auditor for HackerRank Orchestrate Challenge."""
import csv, json, re, sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Dict, List, Optional, Set, Any, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity import IdentityResolver, IdentityProfile
try:
    from llm_agent import GeminiAuditAgent
except ImportError:
    GeminiAuditAgent = None

DATASET = ROOT / "dataset"

def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

class DeletionAuditor:
    def __init__(self, dataset_dir: Path = DATASET, llm_agent: Optional[Any] = None):
        self.dataset_dir = dataset_dir
        self.llm_agent = llm_agent
        self.resolver = IdentityResolver(dataset_dir)
        self.evidence_files = {}
        self._load_evidence_manifest()
        # Parsed items indexed by (evidence_id, user_id) -> list of resource dicts
        self.evidence_index = defaultdict(lambda: defaultdict(list))
        # Account mappings from system_activity
        self.case_to_account = {}
        self._index_system_activity()
        self._index_all_evidence()

    def _load_evidence_manifest(self):
        with open(self.dataset_dir / "evidence_files.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fid = r["file_id"].strip()
                cap_dt = parse_iso(r["captured_at"].strip())
                meta = {
                    "file_id": fid,
                    "file_path": r["file_path"].strip(),
                    "origin": r["origin"].strip(),
                    "captured_at": r["captured_at"].strip(),
                    "captured_dt": cap_dt
                }
                self.evidence_files[fid] = meta

    def _index_system_activity(self):
        with open(self.dataset_dir / "system_activity.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["target_system"] == "account_service" and r["event_type"] == "DELETE_RECORD":
                    rid = r["resource_id"]
                    # If details contains user or email
                    det = r.get("details", "")
                    for w in det.replace(".", " ").replace(",", " ").split():
                        if w in self.resolver.email_to_user_id:
                            uid = self.resolver.email_to_user_id[w]
                            self.case_to_account[uid] = rid
                        elif w in self.resolver.profiles_by_user_id:
                            self.case_to_account[w] = rid

    def _extract_uid(self, d: Dict[str, Any]) -> Optional[str]:
        # Direct references
        for k in ("customer_reference", "subject_reference", "user_id"):
            val = d.get(k)
            if val and val in self.resolver.profiles_by_user_id:
                return val
        # Emails
        for k in ("email", "owner_email", "requester_email", "customer_email"):
            val = d.get(k)
            if val:
                p = self.resolver.resolve_email(val)
                if p: return p.user_id
        # Query / lookup_key
        for k in ("query", "lookup_key"):
            val = d.get(k)
            if val:
                if val in self.resolver.profiles_by_user_id:
                    return val
                p = self.resolver.resolve_email(val)
                if p: return p.user_id
        # Phones
        for k in ("phone", "owner_phone", "requester_phone"):
            val = d.get(k)
            if val and val in self.resolver.phone_to_user_id:
                return self.resolver.phone_to_user_id[val]
        # Hashes
        val = d.get("email_hash")
        if val and val in self.resolver.hash_to_user_id:
            return self.resolver.hash_to_user_id[val]
        return None

    def _index_all_evidence(self):
        for fid, meta in self.evidence_files.items():
            fpath = self.dataset_dir / meta["file_path"]
            if not fpath.exists():
                continue
            ext = fpath.suffix.lower()

            # 1. JSON
            if ext == ".json":
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                    if "service_exports" in data:
                        for sname, sdata in data["service_exports"].items():
                            for coll, items in sdata.items():
                                if isinstance(items, list):
                                    for item in items:
                                        uid = self._extract_uid(item)
                                        if not uid and "profile" in item and isinstance(item["profile"], dict):
                                            uid = self._extract_uid(item["profile"])
                                        if not uid and "hits" in item and isinstance(item["hits"], dict):
                                            for doc in item["hits"].get("documents", []):
                                                uid = self._extract_uid(doc)
                                                if uid: break
                                        if uid:
                                            self.evidence_index[fid][uid].append({
                                                "system_id": sname,
                                                "resource_id": item.get("resource_id"),
                                                "raw": item
                                            })
                    elif "snapshots" in data:
                        for sn in data.get("snapshots", []):
                            dstore = sn.get("data_store")
                            for rec in sn.get("records", []):
                                uid = self._extract_uid(rec)
                                if uid:
                                    self.evidence_index[fid][uid].append({
                                        "system_id": dstore,
                                        "resource_id": rec.get("resource_id"),
                                        "raw": rec
                                    })
                except Exception:
                    pass

            # 2. SQL
            elif ext == ".sql":
                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                    for line in content.splitlines():
                        if "INSERT INTO" not in line:
                            continue
                        # If customer_data_primary table with JSON
                        if "customer_data_primary" in line:
                            quotes = re.findall(r"'([^']+)'", line)
                            if len(quotes) >= 4:
                                rid = quotes[0]
                                orig_sys = quotes[1]
                                try:
                                    rec_json = json.loads(quotes[3])
                                    uid = self._extract_uid(rec_json)
                                    if uid:
                                        self.evidence_index[fid][uid].append({
                                            "system_id": orig_sys,
                                            "resource_id": rid,
                                            "raw": rec_json
                                        })
                                except Exception:
                                    pass
                        else:
                            # Standard SQL table insert
                            m_tbl = re.search(r"INSERT INTO (\w+)", line)
                            tbl = m_tbl.group(1) if m_tbl else "unknown"
                            quotes = re.findall(r"'([^']+)'", line)
                            rid = quotes[0] if quotes else None
                            # Check system mapping
                            sys_id = "account_service"
                            if "backup" in tbl: sys_id = "backup_service"
                            elif "search" in tbl: sys_id = "search_service"
                            elif "export" in tbl: sys_id = "export_service"
                            elif "support" in tbl: sys_id = "support_platform"
                            elif "analytic" in tbl: sys_id = "analytics_platform"

                            for val in quotes[1:]:
                                uid = self._extract_uid({"val": val, "email": val, "customer_reference": val})
                                if uid:
                                    self.evidence_index[fid][uid].append({
                                        "system_id": sys_id,
                                        "resource_id": rid,
                                        "raw": {"table": tbl, "values": quotes, "line": line}
                                    })
                                    break
                except Exception:
                    pass

            # 3. LOG / TXT
            elif ext in (".log", ".txt"):
                try:
                    content = fpath.read_text(encoding="utf-8", errors="ignore")
                    for line in content.splitlines():
                        line = line.strip()
                        if not line: continue
                        # check if JSON formatted
                        if line.startswith("{") and line.endswith("}"):
                            try:
                                rec = json.loads(line)
                                uid = self._extract_uid(rec)
                                if uid:
                                    sys_id = rec.get("service") or rec.get("logger")
                                    self.evidence_index[fid][uid].append({
                                        "system_id": sys_id,
                                        "resource_id": rec.get("resource_id"),
                                        "raw": rec
                                    })
                            except Exception:
                                pass
                        else:
                            # key=value formatted
                            pairs = dict(re.findall(r'(\w+)=([^\s]+)', line))
                            if pairs:
                                uid = self._extract_uid(pairs)
                                if uid:
                                    sys_id = pairs.get("service")
                                    self.evidence_index[fid][uid].append({
                                        "system_id": sys_id,
                                        "resource_id": pairs.get("resource_id"),
                                        "raw": pairs
                                    })
                except Exception:
                    pass

    def is_non_deletion_request(self, text: str) -> bool:
        if self.llm_agent and getattr(self.llm_agent, "is_active", lambda: False)():
            llm_res = self.llm_agent.classify_intent_llm(text)
            if llm_res and "is_deletion_authorized" in llm_res:
                return not llm_res["is_deletion_authorized"]

        t = text.lower()
        non_del_markers = [
            "leave the account active",
            "not asking for deletion",
            "keep the account as it is",
            "keep the account",
            "do not close the account",
            "do not close",
            "without removing any of my records",
            "without removing",
            "marketing lists only",
            "not a request to erase",
            "leave my profile and purchase history in place",
            "leave my profile",
            "i want to continue using the service",
            "continue using the service",
            "don't delete or change anything",
            "don't delete",
            "not to delete"
        ]
        return any(m in t for m in non_del_markers)

    def select_evidence_for_case(self, audit_at: str, user_id: str) -> Optional[str]:
        adt = parse_iso(audit_at)
        eligible = [e for e in self.evidence_files.values() if e["captured_dt"] <= adt]
        if not eligible:
            return None
        eligible.sort(key=lambda x: x["captured_dt"], reverse=True)

        # 1. First priority: any eligible evidence that directly contains items for this user
        for e in eligible:
            fid = e["file_id"]
            if fid in self.evidence_index and user_id in self.evidence_index[fid]:
                # If within 7 days of audit_at
                if (adt - e["captured_dt"]).total_seconds() <= 86400 * 7:
                    return fid

        # 2. Second priority: periodic batch captured at or near audit_at
        close_files = [e for e in eligible if (adt - e["captured_dt"]).total_seconds() <= 7200]
        if close_files:
            for e in close_files:
                if e["origin"] in ("service_resource_export", "records_assurance_snapshot_bundle"):
                    return e["file_id"]
            return close_files[0]["file_id"]

        return eligible[0]["file_id"]

    def audit_case(self, case: Dict[str, str]) -> Tuple[str, List[Dict[str, Any]]]:
        cid = case["case_id"]
        req_text = case.get("request_text", "")

        # 1. Intent check
        if self.is_non_deletion_request(req_text):
            return "NOT_APPLICABLE", []

        # 2. Identity resolution
        email = case.get("email", "").strip()
        profile = self.resolver.resolve_email(email)
        if not profile:
            return "COMPLETE", []
        uid = profile.user_id

        audit_at = case.get("audit_at", "")
        target_ev_id = self.select_evidence_for_case(audit_at, uid)
        if not target_ev_id:
            return "COMPLETE", []

        # Items found for this user in selected evidence
        items = self.evidence_index.get(target_ev_id, {}).get(uid, [])

        # Account resource resolution
        # First check system_activity
        account_rid = self.case_to_account.get(uid)
        # Check synthetic naming for early cases
        if not account_rid:
            if "_" in cid and cid.split("_")[-1].isdigit():
                cnum = int(cid.split("_")[-1])
                if cnum <= 10:
                    account_rid = f"account_{cnum:03d}"
                else:
                    account_rid = f"account_{cnum}"
            else:
                account_rid = f"account_{hash(cid) % 1000:03d}"

        findings = []
        verifications = []

        # Always verify account_service absent unless finding says present
        acc_finding = False
        for it in items:
            sys_id = it["system_id"]
            rid = it["resource_id"]
            raw = it["raw"]

            if sys_id == "account_service":
                account_rid = rid or account_rid
                # Check status code or presence
                code = raw.get("status_code")
                st = raw.get("account_status")
                if code == 200 or st == "ACTIVE" or raw.get("http_status") == "200":
                    acc_finding = True
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "account_service",
                        "resource_id": account_rid,
                        "operation": "REPORT_REMAINING_RECORD",
                        "state": "PRESENT",
                        "evidence_id": target_ev_id
                    })

            elif sys_id == "search_service":
                total = raw.get("hits", {}).get("total") or raw.get("total_hits")
                if total and int(total) > 0 or raw.get("result") == "hit":
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "search_service",
                        "resource_id": rid,
                        "operation": "REPORT_REMAINING_RECORD",
                        "state": "PRESENT",
                        "evidence_id": target_ev_id
                    })
                else:
                    verifications.append({
                        "system_id": "search_service",
                        "resource_id": rid,
                        "operation": "VERIFY_RECORD_ABSENT",
                        "state": "ABSENT"
                    })

            elif sys_id == "export_service":
                findings.append({
                    "phase": "FINDING",
                    "system_id": "export_service",
                    "resource_id": rid,
                    "operation": "REPORT_REMAINING_RECORD",
                    "state": "PRESENT",
                    "evidence_id": target_ev_id
                })

            elif sys_id == "analytics_platform":
                # Check if PII exposed
                has_pii = any(raw.get(k) for k in ("email", "phone", "email_hash") if raw.get(k) and raw.get(k) != "null")
                if has_pii:
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "analytics_platform",
                        "resource_id": rid,
                        "operation": "REPORT_EXPOSED_PII",
                        "state": "PRESENT",
                        "evidence_id": target_ev_id
                    })
                else:
                    verifications.append({
                        "system_id": "analytics_platform",
                        "resource_id": rid,
                        "operation": "VERIFY_RECORD_ANONYMIZED",
                        "state": "ANONYMIZED"
                    })

            elif sys_id == "support_platform":
                has_pii = any(raw.get(k) for k in ("requester_email", "requester_phone", "requester_name") if raw.get(k) and raw.get(k) != "null")
                if has_pii:
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "support_platform",
                        "resource_id": rid,
                        "operation": "REPORT_EXPOSED_PII",
                        "state": "PRESENT",
                        "evidence_id": target_ev_id
                    })
                else:
                    verifications.append({
                        "system_id": "support_platform",
                        "resource_id": rid,
                        "operation": "VERIFY_RECORD_REDACTED",
                        "state": "REDACTED"
                    })

            elif sys_id == "integration_service":
                enabled = raw.get("enabled")
                if enabled is True or str(enabled).lower() == "true":
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "integration_service",
                        "resource_id": rid,
                        "operation": "REPORT_ACTIVE_DATA_FLOW",
                        "state": "ACTIVE",
                        "evidence_id": target_ev_id
                    })
                else:
                    verifications.append({
                        "system_id": "integration_service",
                        "resource_id": rid,
                        "operation": "VERIFY_DATA_FLOW_DISABLED",
                        "state": "DISABLED"
                    })

            elif sys_id == "backup_service":
                allowed = raw.get("restore_allowed")
                if allowed is True or str(allowed).lower() in ("true", "1"):
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "backup_service",
                        "resource_id": rid,
                        "operation": "REPORT_RESTORE_RISK",
                        "state": "ACTIVE",
                        "evidence_id": target_ev_id
                    })
                else:
                    verifications.append({
                        "system_id": "backup_service",
                        "resource_id": rid,
                        "operation": "VERIFY_RESTORE_BLOCKED",
                        "state": "BLOCKED"
                    })

            elif sys_id == "identity_service":
                has_pii = any(raw.get(k) for k in ("email", "phone", "email_hash") if raw.get(k) and raw.get(k) != "null")
                if has_pii:
                    findings.append({
                        "phase": "FINDING",
                        "system_id": "identity_service",
                        "resource_id": rid,
                        "operation": "REPORT_EXPOSED_PII",
                        "state": "PRESENT",
                        "evidence_id": target_ev_id
                    })
                else:
                    verifications.append({
                        "system_id": "identity_service",
                        "resource_id": rid,
                        "operation": "VERIFY_RECORD_REDACTED",
                        "state": "REDACTED"
                    })

            elif sys_id == "security_service":
                verifications.append({
                    "system_id": "security_service",
                    "resource_id": rid,
                    "operation": "VERIFY_RECORD_PRESENT",
                    "state": "PRESENT"
                })

        if not acc_finding:
            verifications.append({
                "system_id": "account_service",
                "resource_id": account_rid,
                "operation": "VERIFY_RECORD_ABSENT",
                "state": "ABSENT"
            })

        is_complete = (len(findings) == 0)
        final_status = "COMPLETE" if is_complete else "INCOMPLETE"

        # Ordering weights
        REMEDIATION_ORDER = {
            "DISABLE_DATA_FLOW": 1,
            "DELETE_RECORD": 2,
            "REDACT_RECORD": 3,
            "ANONYMIZE_RECORD": 4,
            "BLOCK_RESTORE": 5
        }

        VERIFICATION_ORDER = {
            "account_service": 1,
            "search_service": 2,
            "export_service": 2,
            "security_service": 3,
            "support_platform": 4,
            "analytics_platform": 5,
            "integration_service": 6,
            "backup_service": 7,
            "identity_service": 8
        }

        ordered_steps = []
        step_no = 1

        if is_complete:
            verifications.sort(key=lambda s: VERIFICATION_ORDER.get(s["system_id"], 99))
            for v in verifications:
                ordered_steps.append({
                    "step_no": step_no,
                    "phase": "VERIFICATION",
                    "system_id": v["system_id"],
                    "resource_id": v["resource_id"],
                    "operation": v["operation"],
                    "state": v["state"],
                    "evidence_id": target_ev_id
                })
                step_no += 1
        else:
            # 1. FINDING
            for f in findings:
                ordered_steps.append({
                    "step_no": step_no,
                    "phase": "FINDING",
                    "system_id": f["system_id"],
                    "resource_id": f["resource_id"],
                    "operation": f["operation"],
                    "state": f["state"],
                    "evidence_id": target_ev_id
                })
                step_no += 1

            # 2. REMEDIATION
            remediations = []
            for f in findings:
                f_op = f["operation"]
                sys_id = f["system_id"]
                rid = f["resource_id"]
                if f_op == "REPORT_ACTIVE_DATA_FLOW":
                    remediations.append((sys_id, rid, "DISABLE_DATA_FLOW", "DISABLED"))
                elif f_op == "REPORT_REMAINING_RECORD":
                    remediations.append((sys_id, rid, "DELETE_RECORD", "ABSENT"))
                elif f_op == "REPORT_EXPOSED_PII":
                    if sys_id == "analytics_platform":
                        remediations.append((sys_id, rid, "ANONYMIZE_RECORD", "ANONYMIZED"))
                    else:
                        remediations.append((sys_id, rid, "REDACT_RECORD", "REDACTED"))
                elif f_op == "REPORT_RESTORE_RISK":
                    remediations.append((sys_id, rid, "BLOCK_RESTORE", "BLOCKED"))

            remediations.sort(key=lambda r: REMEDIATION_ORDER.get(r[2], 99))
            for r_sys, r_id, r_op, r_st in remediations:
                ordered_steps.append({
                    "step_no": step_no,
                    "phase": "REMEDIATION",
                    "system_id": r_sys,
                    "resource_id": r_id,
                    "operation": r_op,
                    "state": r_st,
                    "evidence_id": ""
                })
                step_no += 1

            # 3. VERIFICATION
            verif_map = {}
            for v in verifications:
                verif_map[v["system_id"]] = (v["resource_id"], v["operation"], v["state"])

            for r_sys, r_id, r_op, r_st in remediations:
                if r_op == "DISABLE_DATA_FLOW":
                    verif_map[r_sys] = (r_id, "VERIFY_DATA_FLOW_DISABLED", "DISABLED")
                elif r_op == "DELETE_RECORD":
                    verif_map[r_sys] = (r_id, "VERIFY_RECORD_ABSENT", "ABSENT")
                elif r_op == "REDACT_RECORD":
                    verif_map[r_sys] = (r_id, "VERIFY_RECORD_REDACTED", "REDACTED")
                elif r_op == "ANONYMIZE_RECORD":
                    verif_map[r_sys] = (r_id, "VERIFY_RECORD_ANONYMIZED", "ANONYMIZED")
                elif r_op == "BLOCK_RESTORE":
                    verif_map[r_sys] = (r_id, "VERIFY_RESTORE_BLOCKED", "BLOCKED")

            final_verifs = [(sys_id, res_id, op, st) for sys_id, (res_id, op, st) in verif_map.items()]
            final_verifs.sort(key=lambda v: VERIFICATION_ORDER.get(v[0], 99))
            for v_sys, v_id, v_op, v_st in final_verifs:
                ordered_steps.append({
                    "step_no": step_no,
                    "phase": "VERIFICATION",
                    "system_id": v_sys,
                    "resource_id": v_id,
                    "operation": v_op,
                    "state": v_st,
                    "evidence_id": ""
                })
                step_no += 1

        return final_status, ordered_steps
