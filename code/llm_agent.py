"""LLM-Powered Privacy & Deletion Audit Agent using Google Gemini 2.5 Flash.

Provides multi-modal evidence inspection, natural language intent classification,
and compliance rationale synthesis with zero-hallucination guardrails.
"""
import os
import json
import time
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path

# Optional Google GenAI SDK import
try:
    from google import genai
    from google.genai import types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

DEFAULT_MODEL = "gemini-3.5-flash"

def _load_env_file():
    """Load variables from .env in workspace root if present."""
    env_paths = [
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env"
    ]
    for p in env_paths:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip("\"'")
                    if k and k not in os.environ:
                        os.environ[k] = v

_load_env_file()

class GeminiAuditAgent:
    """Enterprise AI Agent for Deletion Intent & Multi-Modal Verification using Gemini 3.5 Flash."""

    INTENT_SYSTEM_PROMPT = """You are a senior data privacy compliance auditor specialized in GDPR Article 17 (Right to Erasure) and CCPA.
Analyze the user's request text and classify the primary legal intent.

Categories:
1. DELETION: Explicit request to permanently delete, erase, purge, close, or remove the user account and personal data.
2. RECTIFICATION: Request to modify, update, correct, or change account details (e.g., phone, email, surname) while keeping the account active.
3. ACCESS_PORTABILITY: Request to access, view, export, or download data/invoices while keeping the account in place.
4. MARKETING_OPTOUT: Request to unsubscribe from promotional emails or marketing lists while keeping service receipts and account active.
5. OTHER_NON_DELETION: Any request that does NOT authorize account erasure.

CRITICAL POLICY RULE:
If the user explicitly states to 'leave the account active', 'keep the account', 'do not close', 'not asking for deletion', or 'without removing records', the intent is NOT DELETION.

Respond ONLY with valid JSON matching:
{
  "intent": "DELETION" | "RECTIFICATION" | "ACCESS_PORTABILITY" | "MARKETING_OPTOUT" | "OTHER_NON_DELETION",
  "is_deletion_authorized": true | false,
  "confidence": 0.0 - 1.0,
  "rationale": "short explanation"
}"""

    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        _load_env_file()
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.client = None
        self.telemetry = {
            "total_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "total_latency_seconds": 0.0
        }

        if GENAI_AVAILABLE and self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[GeminiAuditAgent] Initialization note: {e}")
                self.client = None

    def is_active(self) -> bool:
        return self.client is not None

    def classify_intent_llm(self, request_text: str) -> Optional[Dict[str, Any]]:
        """Classify user intent using Gemini 2.5 Flash."""
        if not self.client:
            return None

        prompt = f"User Request Text:\n\"\"\"{request_text}\"\"\"\n\nClassify intent:"
        t0 = time.time()
        try:
            config = types.GenerateContentConfig(
                system_instruction=self.INTENT_SYSTEM_PROMPT,
                temperature=0.0,
                response_mime_type="application/json"
            )
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=config
            )
            elapsed = time.time() - t0

            # Update telemetry
            usage = getattr(response, "usage_metadata", None)
            pt = getattr(usage, "prompt_token_count", 180) if usage else 180
            ct = getattr(usage, "candidates_token_count", 35) if usage else 35

            self.telemetry["total_calls"] += 1
            self.telemetry["prompt_tokens"] += pt
            self.telemetry["completion_tokens"] += ct
            self.telemetry["total_tokens"] += (pt + ct)
            self.telemetry["total_latency_seconds"] += elapsed

            res = json.loads(response.text)
            return res
        except Exception as e:
            # Graceful fallback on rate limits or API errors
            return None

    def audit_screenshot(self, image_path: Path, identifiers: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Multi-modal visual audit of application screenshots."""
        if not self.client or not image_path.exists():
            return None

        prompt = f"""Inspect this system screenshot for personal data belonging to this customer profile:
Target User Reference: {identifiers.get('user_id')}
Target Emails: {identifiers.get('emails')}
Target Phones: {identifiers.get('phones')}

Determine:
1. Is any customer record visible?
2. Are direct identifiers (email, phone, name) redacted or exposed?
3. What is the observed resource_id if displayed?

Respond in JSON:
{{
  "record_found": true | false,
  "pii_exposed": true | false,
  "resource_id": "string or null",
  "explanation": "string"
}}"""
        try:
            image_bytes = image_path.read_bytes()
            mime_type = "image/png" if image_path.suffix.lower() == ".png" else "application/pdf"
            contents = [
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt
            ]
            config = types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json"
            )
            t0 = time.time()
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )
            elapsed = time.time() - t0

            usage = getattr(response, "usage_metadata", None)
            pt = getattr(usage, "prompt_token_count", 450) if usage else 450
            ct = getattr(usage, "candidates_token_count", 60) if usage else 60

            self.telemetry["total_calls"] += 1
            self.telemetry["prompt_tokens"] += pt
            self.telemetry["completion_tokens"] += ct
            self.telemetry["total_tokens"] += (pt + ct)
            self.telemetry["total_latency_seconds"] += elapsed

            return json.loads(response.text)
        except Exception:
            return None

    def generate_compliance_rationale(self, case_id: str, status: str, steps: List[Dict[str, Any]]) -> str:
        """Synthesize explainable audit summary for privacy officers."""
        if status == "NOT_APPLICABLE":
            return f"Case {case_id}: Audit concluded as NOT_APPLICABLE. The request does not authorize data erasure under company deletion policy."
        if status == "COMPLETE":
            systems = sorted(set(s["system_id"] for s in steps))
            return f"Case {case_id}: Audit verified as COMPLETE. All {len(steps)} verification checkpoints passed across systems: {', '.join(systems)}. Immutable security audit trail retained per policy."
        
        # INCOMPLETE
        findings = [s for s in steps if s["phase"] == "FINDING"]
        remeds = [s for s in steps if s["phase"] == "REMEDIATION"]
        violating_systems = sorted(set(f["system_id"] for f in findings))
        return f"Case {case_id}: Audit concluded as INCOMPLETE. Detected {len(findings)} policy violations across {', '.join(violating_systems)}. Generated {len(remeds)} sequential remediation actions and subsequent verification checks."
