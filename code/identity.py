"""Identity Resolution Module for HackerRank Orchestrate Deletion Audit Agent."""
import csv
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, List

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"

@dataclass
class IdentityProfile:
    user_id: str
    name: Optional[str] = None
    email: Optional[str] = None
    all_emails: Set[str] = field(default_factory=set)
    phone: Optional[str] = None
    all_phones: Set[str] = field(default_factory=set)
    email_hash: Optional[str] = None
    all_email_hashes: Set[str] = field(default_factory=set)

class IdentityResolver:
    def __init__(self, dataset_dir: Path = DATASET):
        self.dataset_dir = dataset_dir
        self.profiles_by_user_id: Dict[str, IdentityProfile] = {}
        self.email_to_user_id: Dict[str, str] = {}
        self.phone_to_user_id: Dict[str, str] = {}
        self.hash_to_user_id: Dict[str, str] = {}
        self._load_identities()
        self._load_audit_log()

    def _load_identities(self):
        identities_file = self.dataset_dir / "user_identities.csv"
        with open(identities_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                email = row["email"].strip().lower() if row.get("email") else None
                phone = row["phone"].strip() if row.get("phone") else None
                ehash = row["email_hash"].strip().lower() if row.get("email_hash") else None
                name = row["name"].strip() if row.get("name") else None

                profile = IdentityProfile(
                    user_id=uid,
                    name=name,
                    email=email,
                    phone=phone,
                    email_hash=ehash
                )
                if email:
                    profile.all_emails.add(email)
                    self.email_to_user_id[email] = uid
                if phone:
                    profile.all_phones.add(phone)
                    self.phone_to_user_id[phone] = uid
                if ehash:
                    profile.all_email_hashes.add(ehash)
                    self.hash_to_user_id[ehash] = uid

                self.profiles_by_user_id[uid] = profile

    def _load_audit_log(self):
        audit_file = self.dataset_dir / "user_audit_log.csv"
        with open(audit_file, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                uid = row["user_id"].strip()
                field_name = row["field_name"].strip().lower()
                old_val = row["old_value"].strip() if row.get("old_value") else ""
                new_val = row["new_value"].strip() if row.get("new_value") else ""

                if uid not in self.profiles_by_user_id:
                    self.profiles_by_user_id[uid] = IdentityProfile(user_id=uid)
                profile = self.profiles_by_user_id[uid]

                if field_name == "email":
                    for val in (old_val, new_val):
                        if val:
                            val_lower = val.lower()
                            profile.all_emails.add(val_lower)
                            if val_lower not in self.email_to_user_id:
                                self.email_to_user_id[val_lower] = uid
                elif field_name == "phone":
                    for val in (old_val, new_val):
                        if val:
                            profile.all_phones.add(val)
                            if val not in self.phone_to_user_id:
                                self.phone_to_user_id[val] = uid
                elif field_name == "email_hash":
                    for val in (old_val, new_val):
                        if val:
                            val_lower = val.lower()
                            profile.all_email_hashes.add(val_lower)
                            if val_lower not in self.hash_to_user_id:
                                self.hash_to_user_id[val_lower] = uid
                elif field_name == "name":
                    if new_val and not profile.name:
                        profile.name = new_val

    def resolve_email(self, email: str) -> Optional[IdentityProfile]:
        email_clean = email.strip().lower()
        uid = self.email_to_user_id.get(email_clean)
        if uid:
            return self.profiles_by_user_id.get(uid)
        return None

    def resolve_user_id(self, user_id: str) -> Optional[IdentityProfile]:
        return self.profiles_by_user_id.get(user_id.strip())

if __name__ == "__main__":
    resolver = IdentityResolver()
    print(f"Loaded {len(resolver.profiles_by_user_id)} user profiles.")
    print(f"Indexed {len(resolver.email_to_user_id)} unique emails.")
    # Verify all 518 cases in deletion_requests.csv
    with open(DATASET / "deletion_requests.csv", newline="", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))
    unresolved = []
    for c in cases:
        em = c["email"].strip()
        p = resolver.resolve_email(em)
        if not p:
            unresolved.append(c)
    print(f"Checked 518 deletion requests: resolved={len(cases)-len(unresolved)}, unresolved={len(unresolved)}")
    if unresolved:
        print("Unresolved cases:", [c["case_id"] for c in unresolved])
    else:
        print("ALL 518 DELETION REQUEST EMAILS RESOLVED (100% COVERAGE)!")
