"""Evaluation Suite for HackerRank Orchestrate Challenge.

Computes case accuracy, step-level metrics, sequence ordering validity,
and executes 3 team-created edge test cases.
"""
import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple, Any

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "code"))
from auditor import DeletionAuditor

DATASET = ROOT / "dataset"

class EvaluationRunner:
    def __init__(self, dataset_dir: Path = DATASET):
        self.dataset_dir = dataset_dir
        self.auditor = DeletionAuditor(dataset_dir)

    def evaluate_sample_benchmark(self) -> Dict[str, Any]:
        """Evaluate against the 32 gold-standard sample cases."""
        sample_reqs_path = self.dataset_dir / "sample_deletion_requests.csv"
        sample_out_path = self.dataset_dir / "sample_output.csv"

        with open(sample_reqs_path, encoding="utf-8") as f:
            sample_reqs = list(csv.DictReader(f))

        with open(sample_out_path, encoding="utf-8") as f:
            ground_truth = {r["case_id"]: r for r in csv.DictReader(f)}

        total_cases = len(sample_reqs)
        status_matches = 0
        step_count_matches = 0
        total_gt_steps = 0
        field_matches = {
            "phase": 0,
            "system_id": 0,
            "resource_id": 0,
            "operation": 0,
            "state": 0,
            "evidence_id": 0
        }
        ordering_valid_cases = 0

        phase_rank = {"FINDING": 1, "REMEDIATION": 2, "VERIFICATION": 3}

        for req in sample_reqs:
            cid = req["case_id"]
            gt = ground_truth[cid]
            gt_status = gt["deletion_status"]
            gt_steps = json.loads(gt["steps_json"])

            pred_status, pred_steps = self.auditor.audit_case(req)

            # Check status match
            if pred_status == gt_status:
                status_matches += 1

            if len(pred_steps) == len(gt_steps):
                step_count_matches += 1

            # Check ordering validity
            ranks = [phase_rank.get(s["phase"], 99) for s in pred_steps]
            is_valid_order = all(ranks[i] <= ranks[i+1] for i in range(len(ranks) - 1))
            if is_valid_order:
                ordering_valid_cases += 1

            # Field-level comparison
            total_gt_steps += len(gt_steps)
            min_steps = min(len(pred_steps), len(gt_steps))
            for i in range(min_steps):
                p_s = pred_steps[i]
                g_s = gt_steps[i]
                for fld in field_matches:
                    if p_s.get(fld) == g_s.get(fld):
                        field_matches[fld] += 1

        field_acc = {fld: (count / total_gt_steps if total_gt_steps else 0.0) for fld, count in field_matches.items()}

        return {
            "total_cases": total_cases,
            "status_accuracy": status_matches / total_cases,
            "status_matches": status_matches,
            "step_count_matches": step_count_matches,
            "step_count_accuracy": step_count_matches / total_cases,
            "ordering_validity": ordering_valid_cases / total_cases,
            "field_accuracies": field_acc,
            "total_gt_steps": total_gt_steps
        }

    def run_custom_edge_cases(self) -> List[Dict[str, Any]]:
        """Run 3 team-created evaluation edge cases."""
        test_cases = [
            {
                "name": "Edge Case 1: Active Connector Data Recreation Risk",
                "description": "User requests deletion; account is deleted but active connector 'data_flow_1770' in integration_service remains enabled.",
                "input": {
                    "case_id": "custom_edge_01",
                    "email": "akosua.gyasi@gmail.com",
                    "requested_at": "2026-09-08T08:00:00Z",
                    "reported_status": "COMPLETE",
                    "reported_at": "2026-09-09T08:00:00Z",
                    "audit_at": "2026-09-26T11:00:00Z",
                    "request_text": "Please delete my account and all associated personal data."
                },
                "expected_status": "INCOMPLETE",
                "must_contain_phase": ["FINDING", "REMEDIATION", "VERIFICATION"],
                "must_contain_op": ["REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"]
            },
            {
                "name": "Edge Case 2: Non-Deletion Account Modification Request",
                "description": "User requests mobile number update and explicitly states to leave account active.",
                "input": {
                    "case_id": "custom_edge_02",
                    "email": "kwame.mensah@gmail.com",
                    "requested_at": "2026-09-08T12:00:00Z",
                    "reported_status": "COMPLETE",
                    "reported_at": "2026-09-09T12:00:00Z",
                    "audit_at": "2026-10-01T12:00:00Z",
                    "request_text": "The mobile number on my profile is out of date. Please replace it with the new one and leave the account active."
                },
                "expected_status": "NOT_APPLICABLE",
                "expected_steps_count": 0
            },
            {
                "name": "Edge Case 3: Retained Identity Tombstone with Exposed PII",
                "description": "Account is removed, but identity_service tombstone retains unredacted email/phone.",
                "input": {
                    "case_id": "custom_edge_03",
                    "email": "addo.kofi@gmail.com",
                    "requested_at": "2026-09-05T08:00:00Z",
                    "reported_status": "COMPLETE",
                    "reported_at": "2026-09-06T08:00:00Z",
                    "audit_at": "2026-09-15T12:00:00Z",
                    "request_text": "Please permanently erase my account and contact details."
                },
                "expected_status": "INCOMPLETE",
                "must_contain_phase": ["FINDING", "REMEDIATION", "VERIFICATION"],
                "must_contain_op": ["REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"]
            }
        ]

        results = []
        for tc in test_cases:
            pred_status, pred_steps = self.auditor.audit_case(tc["input"])
            passed = (pred_status == tc["expected_status"])
            if "expected_steps_count" in tc:
                passed = passed and (len(pred_steps) == tc["expected_steps_count"])
            if "must_contain_phase" in tc:
                present_phases = set(s["phase"] for s in pred_steps)
                passed = passed and all(p in present_phases for p in tc["must_contain_phase"])
            if "must_contain_op" in tc:
                present_ops = set(s["operation"] for s in pred_steps)
                passed = passed and all(op in present_ops for op in tc["must_contain_op"])

            results.append({
                "name": tc["name"],
                "description": tc["description"],
                "passed": passed,
                "pred_status": pred_status,
                "pred_steps_count": len(pred_steps)
            })

        return results

def main():
    print("=" * 70)
    print("RUNNING AGENT EVALUATION SUITE")
    print("=" * 70)
    runner = EvaluationRunner()

    print("\n[1/2] Benchmarking against 32 Gold-Standard Sample Cases...")
    metrics = runner.evaluate_sample_benchmark()
    print(f"  Status Accuracy:      {metrics['status_accuracy']*100:.1f}% ({metrics['status_matches']}/{metrics['total_cases']})")
    print(f"  Step Count Match:     {metrics['step_count_accuracy']*100:.1f}% ({metrics['step_count_matches']}/{metrics['total_cases']})")
    print(f"  Ordering Validity:    {metrics['ordering_validity']*100:.1f}%")
    print("  Field Accuracies:")
    for fld, acc in metrics["field_accuracies"].items():
        print(f"    - {fld:<12}: {acc*100:.1f}%")

    print("\n[2/2] Executing 3 Team-Created Custom Edge Cases...")
    custom_results = runner.run_custom_edge_cases()
    all_passed = True
    for cr in custom_results:
        status_icon = "PASS" if cr["passed"] else "FAIL"
        print(f"  [{status_icon}] {cr['name']}")
        print(f"         Status: {cr['pred_status']}, Steps: {cr['pred_steps_count']}")
        if not cr["passed"]:
            all_passed = False

    print("\n" + "=" * 70)
    if all_passed:
        print("ALL EVALUATION SUITE CHECKS PASSED SUCCESSFULLY!")
    else:
        print("EVALUATION SUITE COMPLETED WITH WARNINGS.")
    print("=" * 70)

if __name__ == "__main__":
    main()
