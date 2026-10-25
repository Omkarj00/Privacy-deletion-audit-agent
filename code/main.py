"""Main entry point for HackerRank Orchestrate Deletion Audit Agent.

Processes dataset/deletion_requests.csv and writes output.csv in root repository.
"""
import csv
import json
import os
import sys
import time
from pathlib import Path

# Ensure code directory is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse
from auditor import DeletionAuditor
try:
    from llm_agent import GeminiAuditAgent
except ImportError:
    GeminiAuditAgent = None

def main():
    parser = argparse.ArgumentParser(description="Automated Privacy & Deletion Audit Agent")
    parser.add_argument("--mode", choices=["deterministic", "hybrid"], default="deterministic",
                        help="Execution mode: deterministic (zero-hallucination fast engine) or hybrid (Gemini Flash intent & XAI)")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of cases to process (for debugging)")
    parser.add_argument("--explain", action="store_true", help="Generate explainable audit rationales")
    args = parser.parse_args()

    start_time = time.time()
    dataset_dir = BASE_DIR / "dataset"
    requests_path = dataset_dir / "deletion_requests.csv"
    output_root_path = BASE_DIR / "output.csv"
    output_dataset_path = dataset_dir / "output.csv"

    print("=" * 70)
    print("HACKERRANK ORCHESTRATE: AUTOMATED DELETION AUDIT AGENT")
    print("=" * 70)
    print(f"Architecture Mode: {args.mode.upper()}")

    llm_agent = None
    if args.mode == "hybrid" and GeminiAuditAgent:
        llm_agent = GeminiAuditAgent()
        if llm_agent.is_active():
            print(f"AI Engine: ACTIVE (Model: {llm_agent.model_name})")
        else:
            print("AI Engine: STANDBY (No GEMINI_API_KEY detected; using deterministic fallback)")
    else:
        print("AI Engine: DETERMINISTIC (Zero-hallucination compliance engine)")

    print(f"Loading dataset from: {dataset_dir}")
    print(f"Processing requests from: {requests_path}")

    auditor = DeletionAuditor(dataset_dir, llm_agent=llm_agent)

    with open(requests_path, newline="", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))

    # Sort cases numerically by case_id
    cases.sort(key=lambda c: int(c["case_id"].split("_")[1]))
    if args.limit:
        cases = cases[:args.limit]
    total_cases = len(cases)
    print(f"Total cases to audit: {total_cases}")

    results = []
    status_counts = {"COMPLETE": 0, "INCOMPLETE": 0, "NOT_APPLICABLE": 0}

    for idx, case in enumerate(cases, 1):
        cid = case["case_id"]
        status, steps = auditor.audit_case(case)
        status_counts[status] = status_counts.get(status, 0) + 1
        steps_json_str = json.dumps(steps, separators=(",", ":"))
        results.append({
            "case_id": cid,
            "deletion_status": status,
            "steps_json": steps_json_str
        })
        if idx % 100 == 0 or idx == total_cases:
            print(f"[{idx:>3}/{total_cases}] Processed {cid} -> {status} ({len(steps)} steps)")

    # Write output to root output.csv
    fieldnames = ["case_id", "deletion_status", "steps_json"]
    for out_path in (output_root_path, output_dataset_path):
        with open(out_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in results:
                writer.writerow(r)
        print(f"Saved {len(results)} rows to: {out_path}")

    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print("AUDIT EXECUTION SUMMARY")
    print("=" * 70)
    print(f"Total cases processed: {total_cases}")
    print(f"  COMPLETE:       {status_counts.get('COMPLETE', 0):>4} ({status_counts.get('COMPLETE', 0)/total_cases*100:.1f}%)")
    print(f"  INCOMPLETE:     {status_counts.get('INCOMPLETE', 0):>4} ({status_counts.get('INCOMPLETE', 0)/total_cases*100:.1f}%)")
    print(f"  NOT_APPLICABLE: {status_counts.get('NOT_APPLICABLE', 0):>4} ({status_counts.get('NOT_APPLICABLE', 0)/total_cases*100:.1f}%)")
    print(f"Execution time:   {elapsed:.2f} seconds")
    print("=" * 70)

if __name__ == "__main__":
    main()
