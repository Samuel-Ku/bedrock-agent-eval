"""The twenty evaluation cases.

This module is the single source of truth for the fixtures. `evals/run_evals.py` reads
them directly, and `evals/export_native_dataset.py` exports them into the JSONL shape
the native Bedrock evaluation job requires (`prompt` + `referenceResponse`), so the two
evaluation paths cannot drift apart.

Why synthetic, and why authored rather than collected: the assertions have to be
objective. Real meeting transcripts would require a human to label ground truth, and
then the pass rate would measure the labelling, not the model. Here the author of the
input is the author of the expected output, so every assertion is decidable.

The PII in these fixtures is deliberately fabricated and uses reserved ranges:
`example.com` addresses and the 4111-test card prefix. No real personal data appears in
this repository, and none of it comes from any employer.

Each case carries:
  expected.tasks      list of {title, priority, owner} — scored by fuzzy title match
  expected.decisions  substrings that must appear among the extracted decisions
  expected.risks      substrings that must appear among the extracted risks
  strict_extras       when True, proposing tasks the meeting did not agree is an error
  pii                 exact strings that must NOT survive into the model's output
"""

from __future__ import annotations

from typing import Any

CASES: list[dict[str, Any]] = [
    {
        "id": "case-01",
        "project_key": "PAY",
        "sprint": 24,
        "team": "Payments Platform",
        "transcript": (
            "Marek: The card authorisation retry we shipped last sprint is dropping about "
            "2% of attempts under load. This is a blocker for the merchant pilot.\n"
            "Anna: I can add the retry with exponential backoff. I will take that today.\n"
            "Marek: Good. Also the settlement report is a day late every week — normal "
            "priority, next sprint is fine.\n"
            "Anna: I will pick up the settlement report after the retry lands.\n"
            "Decision: we ship the retry behind a feature flag before the pilot."
        ),
        "existing_issues": [{"key": "PAY-1180", "title": "Add retry to card authorisation"}],
        "expected": {
            "tasks": [
                {
                    "title": "Add retry with exponential backoff to card authorisation",
                    "priority": "critical",
                    "owner": "Anna",
                },
                {
                    "title": "Fix the late weekly settlement report",
                    "priority": "medium",
                    "owner": "Anna",
                },
            ],
            "decisions": ["ship the retry behind a feature flag before the pilot"],
            "risks": ["2% of attempts", "pilot"],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-02",
        "project_key": "ONB",
        "sprint": 11,
        "team": "Onboarding",
        "transcript": (
            "Tomasz: KYC document upload fails for PDFs over 10 MB. Urgent — support has "
            "twelve tickets open.\n"
            "Ewa: I will raise the upload limit and add a clearer error message.\n"
            "Tomasz: Please also refresh the identity-check vendor contract. That is a "
            "nice to have, no rush.\n"
            "Ewa: Noted, backlog.\n"
            "Decision: we keep the vendor until the new pricing is reviewed."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Raise the PDF upload size limit and improve the error message",
                    "priority": "high",
                    "owner": "Ewa",
                },
                {
                    "title": "Refresh the identity-check vendor contract",
                    "priority": "low",
                    "owner": "Ewa",
                },
            ],
            "decisions": ["keep the vendor until the new pricing is reviewed"],
            "risks": ["twelve tickets"],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-03",
        "project_key": "MOB",
        "sprint": 7,
        "team": "Mobile",
        "transcript": (
            "Kasia: Crash rate on Android 15 doubled after the last release. Critical.\n"
            "Peter: I will bisect the release and ship a hotfix.\n"
            "Kasia: Assign that to yourself and keep me posted hourly.\n"
            "Peter: Will do. The iOS parity work stays medium priority.\n"
            "Decision: hotfix goes out before Friday regardless of the parity work."
        ),
        "existing_issues": [{"key": "MOB-441", "title": "Investigate Android 15 crash rate"}],
        "expected": {
            "tasks": [
                {
                    "title": "Bisect the release and ship a hotfix for the Android 15 crash",
                    "priority": "critical",
                    "owner": "Peter",
                },
                {
                    "title": "iOS parity work",
                    "priority": "medium",
                    "owner": "Peter",
                },
            ],
            "decisions": ["hotfix goes out before Friday"],
            "risks": ["Crash rate on Android 15 doubled"],
        },
        "strict_extras": False,
        "pii": [],
    },
    {
        "id": "case-04",
        "project_key": "CRM",
        "sprint": 31,
        "team": "Customer Platform",
        "transcript": (
            "Lena: Customer Marta Zielinska reports the statement export times out. Her "
            "number is +48 601 234 567 and her address on file is marta.zielinska@example.com.\n"
            "Pawel: I will profile the export query. Urgent, since it blocks her month-end.\n"
            "Lena: Also please remove the test card 4111 1111 1111 1111 from the sandbox "
            "seed data. Normal priority.\n"
            "Pawel: Understood.\n"
            "Decision: no production data in sandbox seeds going forward."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Profile and fix the statement export timeout",
                    "priority": "high",
                    "owner": "Pawel",
                },
                {
                    "title": "Remove the test card from the sandbox seed data",
                    "priority": "medium",
                    "owner": "Pawel",
                },
            ],
            "decisions": ["no production data in sandbox seeds"],
            "risks": ["blocks her month-end"],
        },
        "strict_extras": True,
        "pii": [
            "+48 601 234 567",
            "marta.zielinska@example.com",
            "4111 1111 1111 1111",
        ],
    },
    {
        "id": "case-05",
        "project_key": "PAY",
        "sprint": 24,
        "team": "Payments Platform",
        "transcript": (
            "Marek: We agreed last week to move the payout scheduler to a queue. Nobody "
            "has started it.\n"
            "Anna: I will migrate the scheduler to the queue this sprint. Medium priority.\n"
            "Marek: Also the reconciliation dashboard needs the new status column. Low "
            "priority.\n"
            "Decision: payouts stay on the current scheduler until the queue migration is "
            "verified in staging."
        ),
        "existing_issues": [{"key": "PAY-1201", "title": "Move payout scheduler to a queue"}],
        "expected": {
            "tasks": [
                {
                    "title": "Migrate the payout scheduler to the queue",
                    "priority": "medium",
                    "owner": "Anna",
                },
                {
                    "title": "Add the new status column to the reconciliation dashboard",
                    "priority": "low",
                    "owner": "Anna",
                },
            ],
            "decisions": ["payouts stay on the current scheduler"],
            "risks": [],
        },
        "strict_extras": False,
        "pii": [],
    },
    {
        "id": "case-06",
        "project_key": "SEC",
        "sprint": 3,
        "team": "Security",
        "transcript": (
            "Igor: The dependency scan flagged a critical CVE in the PDF library. This is "
            "a blocker for the release.\n"
            "Nina: I will upgrade the library and re-run the scan. I own that.\n"
            "Igor: Also document the incident response runbook. Low priority, whenever "
            "there is room.\n"
            "Nina: Fine.\n"
            "Decision: releases are blocked until the scan is clean."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Upgrade the PDF library and re-run the dependency scan",
                    "priority": "critical",
                    "owner": "Nina",
                },
                {
                    "title": "Document the incident response runbook",
                    "priority": "low",
                    "owner": "Nina",
                },
            ],
            "decisions": ["releases are blocked until the scan is clean"],
            "risks": ["critical CVE"],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-07",
        "project_key": "ONB",
        "sprint": 11,
        "team": "Onboarding",
        "transcript": (
            "Ewa: Onboarding drop-off at the address step is 40%. That is our biggest "
            "funnel leak.\n"
            "Tomasz: I will instrument the step and find where people abandon. High "
            "priority.\n"
            "Ewa: And I will rewrite the copy for that screen. Medium.\n"
            "Decision: we do not change the address validation rules this quarter."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Instrument the address step to find where users abandon",
                    "priority": "high",
                    "owner": "Tomasz",
                },
                {
                    "title": "Rewrite the copy for the address screen",
                    "priority": "medium",
                    "owner": "Ewa",
                },
            ],
            "decisions": ["do not change the address validation rules"],
            "risks": ["drop-off at the address step is 40%"],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-08",
        "project_key": "MOB",
        "sprint": 7,
        "team": "Mobile",
        "transcript": (
            "Peter: The app store review rejected the build because of the permission "
            "description. Urgent.\n"
            "Kasia: I will rewrite the permission copy and resubmit.\n"
            "Peter: Thank you. The dark mode ticket can wait — nice to have.\n"
            "Decision: we resubmit today and hold the dark mode work."
        ),
        "existing_issues": [{"key": "MOB-460", "title": "Rewrite the permission description"}],
        "expected": {
            "tasks": [
                {
                    "title": "Rewrite the permission copy and resubmit the build",
                    "priority": "high",
                    "owner": "Kasia",
                },
                {
                    "title": "Dark mode",
                    "priority": "low",
                    "owner": "Peter",
                },
            ],
            "decisions": ["resubmit today"],
            "risks": ["app store review rejected the build"],
        },
        "strict_extras": False,
        "pii": [],
    },
    {
        "id": "case-09",
        "project_key": "PAY",
        "sprint": 24,
        "team": "Payments Platform",
        "transcript": "",
        "existing_issues": [],
        "expected": {"tasks": [], "decisions": [], "risks": []},
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-10",
        "project_key": "ONB",
        "sprint": 11,
        "team": "Onboarding",
        "transcript": (
            "Tomasz: Short one today. We are keeping the current KYC provider for another "
            "year. No action items, I just wanted that recorded.\n"
            "Ewa: Agreed, nothing to do."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [],
            "decisions": ["keeping the current KYC provider"],
            "risks": [],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-11",
        "project_key": "CRM",
        "sprint": 31,
        "team": "Customer Platform",
        "transcript": (
            "Lena: We need the churn dashboard. I said I would do it but I am at capacity.\n"
            "Pawel: I will take the churn dashboard instead. High priority for the board "
            "meeting.\n"
            "Lena: Thank you. I will handle the data contract review, medium.\n"
            "Decision: ownership of the churn dashboard moves to Pawel."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Build the churn dashboard",
                    "priority": "high",
                    "owner": "Pawel",
                },
                {
                    "title": "Review the data contract",
                    "priority": "medium",
                    "owner": "Lena",
                },
            ],
            "decisions": ["ownership of the churn dashboard moves to Pawel"],
            "risks": [],
        },
        "strict_extras": False,
        "pii": [],
    },
    {
        "id": "case-12",
        "project_key": "SEC",
        "sprint": 3,
        "team": "Security",
        "transcript": (
            "Igor: We already have a ticket for the dependency scan failure, do not create "
            "another one.\n"
            "Nina: Understood. I will add the SSO session timeout review instead, medium "
            "priority.\n"
            "Decision: the dependency scan stays on the existing ticket."
        ),
        "existing_issues": [
            {"key": "SEC-201", "title": "Upgrade vulnerable dependency and re-run scan"}
        ],
        "expected": {
            "tasks": [
                {
                    "title": "Review the SSO session timeout",
                    "priority": "medium",
                    "owner": "Nina",
                }
            ],
            "decisions": ["stays on the existing ticket"],
            "risks": [],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-13",
        "project_key": "PAY",
        "sprint": 25,
        "team": "Payments Platform",
        "transcript": (
            "Marek: Planning for next sprint. Anna, the refund API pagination is high "
            "priority.\n"
            "Anna: I will do pagination. Also the webhook retry dead-letter queue is a "
            "blocker, I will take that first.\n"
            "Marek: I will update the API documentation, low priority.\n"
            "Anna: And someone needs to rotate the signing keys, urgent.\n"
            "Marek: I will rotate the signing keys.\n"
            "Decision: the dead-letter queue comes before pagination."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Fix the webhook retry dead-letter queue",
                    "priority": "critical",
                    "owner": "Anna",
                },
                {
                    "title": "Add pagination to the refund API",
                    "priority": "high",
                    "owner": "Anna",
                },
                {
                    "title": "Rotate the signing keys",
                    "priority": "high",
                    "owner": "Marek",
                },
                {
                    "title": "Update the API documentation",
                    "priority": "low",
                    "owner": "Marek",
                },
            ],
            "decisions": ["the dead-letter queue comes before pagination"],
            "risks": [],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-14",
        "project_key": "CRM",
        "sprint": 31,
        "team": "Customer Platform",
        "transcript": (
            "Lena: A customer emailed from jan.kowalski@example.com asking us to delete "
            "his account. His phone is +48 602 998 111 and he left the card "
            "4111 1111 1111 1111 on the support thread.\n"
            "Pawel: I will implement the account deletion endpoint, critical, GDPR clock "
            "is running.\n"
            "Lena: I will redact the support thread, medium.\n"
            "Decision: deletion requests are processed within 30 days."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Implement the account deletion endpoint",
                    "priority": "critical",
                    "owner": "Pawel",
                },
                {
                    "title": "Redact the support thread",
                    "priority": "medium",
                    "owner": "Lena",
                },
            ],
            "decisions": ["deletion requests are processed within 30 days"],
            "risks": ["GDPR clock is running"],
        },
        "strict_extras": True,
        "pii": [
            "jan.kowalski@example.com",
            "+48 602 998 111",
            "4111 1111 1111 1111",
        ],
    },
    {
        "id": "case-15",
        "project_key": "ONB",
        "sprint": 12,
        "team": "Onboarding",
        "transcript": (
            "Ewa: Formularz rejestracji gubi dane, gdy użytkownik cofnie się o krok. To "
            "blokuje premierę.\n"
            "Tomasz: Naprawię stan formularza, zajmę się tym dzisiaj. To krytyczne.\n"
            "Ewa: Ja zaktualizuję teksty pomocy, niski priorytet.\n"
            "Decyzja: nie wypuszczamy rejestracji, dopóki błąd nie zostanie naprawiony."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Naprawa stanu formularza rejestracji",
                    "priority": "critical",
                    "owner": "Tomasz",
                },
                {
                    "title": "Aktualizacja tekstów pomocy",
                    "priority": "low",
                    "owner": "Ewa",
                },
            ],
            "decisions": ["nie wypuszczamy rejestracji"],
            "risks": ["gubi dane"],
        },
        "strict_extras": False,
        "pii": [],
    },
    {
        "id": "case-16",
        "project_key": "MOB",
        "sprint": 8,
        "team": "Mobile",
        "transcript": (
            "Kasia: The offline mode is half done. I am not sure whether it is critical or "
            "just important.\n"
            "Peter: Let us call it high for now. I will finish the sync layer.\n"
            "Kasia: Then I will write the offline test plan, medium.\n"
            "Decision: offline mode is high priority for this sprint."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Finish the sync layer for offline mode",
                    "priority": "high",
                    "owner": "Peter",
                },
                {
                    "title": "Write the offline test plan",
                    "priority": "medium",
                    "owner": "Kasia",
                },
            ],
            "decisions": ["offline mode is high priority for this sprint"],
            "risks": [],
        },
        "strict_extras": False,
        "pii": [],
    },
    {
        "id": "case-17",
        "project_key": "SEC",
        "sprint": 3,
        "team": "Security",
        "transcript": (
            "Igor: Two risks. First, we have no log retention beyond 30 days, which will "
            "fail the audit. Second, the staging environment still uses production-like "
            "credentials.\n"
            "Nina: I will extend log retention to one year. Critical for the audit.\n"
            "Igor: I will rotate the staging credentials, urgent.\n"
            "Decision: the audit date does not move."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Extend log retention to one year",
                    "priority": "critical",
                    "owner": "Nina",
                },
                {
                    "title": "Rotate the staging credentials",
                    "priority": "high",
                    "owner": "Igor",
                },
            ],
            "decisions": ["the audit date does not move"],
            "risks": ["no log retention beyond 30 days", "production-like credentials"],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-18",
        "project_key": "PAY",
        "sprint": 25,
        "team": "Payments Platform",
        "transcript": (
            "Marek: Three decisions today. One, we standardise on ISO 20022 for the "
            "settlement feed. Two, the legacy SOAP endpoint is deprecated at year end. "
            "Three, we hire a contractor for the migration.\n"
            "Anna: Agreed on all three. I will draft the ISO 20022 migration plan, medium.\n"
            "Decision: all three confirmed."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Draft the ISO 20022 migration plan",
                    "priority": "medium",
                    "owner": "Anna",
                }
            ],
            "decisions": ["ISO 20022", "SOAP endpoint is deprecated", "hire a contractor"],
            "risks": [],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-19",
        "project_key": "CRM",
        "sprint": 32,
        "team": "Customer Platform",
        "transcript": (
            "Lena: Before we start — did anyone watch the match last night? Extraordinary.\n"
            "Pawel: Unbelievable. Anyway. The customer search returns duplicates. I will "
            "deduplicate on the email field, high priority.\n"
            "Lena: And the support macro editor is broken, medium, I will take it.\n"
            "Pawel: Right. Nothing else from me. Good luck with the match talk.\n"
            "Decision: search deduplication uses the email field only."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Deduplicate customer search on the email field",
                    "priority": "high",
                    "owner": "Pawel",
                },
                {
                    "title": "Fix the support macro editor",
                    "priority": "medium",
                    "owner": "Lena",
                },
            ],
            "decisions": ["search deduplication uses the email field"],
            "risks": [],
        },
        "strict_extras": True,
        "pii": [],
    },
    {
        "id": "case-20",
        "project_key": "ONB",
        "sprint": 12,
        "team": "Onboarding",
        "transcript": (
            "Tomasz: The e-signature callback is retried forever when the customer's email "
            "is on a domain we block. Contact was kamil.wisniewski@example.com, phone "
            "+48 605 771 200.\n"
            "Ewa: I will cap the retry count and alert on exhaustion. High priority.\n"
            "Tomasz: I will update the blocklist policy, medium.\n"
            "Decision: blocked domains get a hard failure instead of a retry."
        ),
        "existing_issues": [],
        "expected": {
            "tasks": [
                {
                    "title": "Cap the e-signature callback retry count and alert on exhaustion",
                    "priority": "high",
                    "owner": "Ewa",
                },
                {
                    "title": "Update the blocklist policy",
                    "priority": "medium",
                    "owner": "Tomasz",
                },
            ],
            "decisions": ["blocked domains get a hard failure instead of a retry"],
            "risks": ["retried forever"],
        },
        "strict_extras": True,
        "pii": ["kamil.wisniewski@example.com", "+48 605 771 200"],
    },
]

_BY_ID = {case["id"]: case for case in CASES}

# Cases registered at runtime, for transcripts that are not fixtures.
#
# The MCP server accepts an arbitrary transcript, and its tools resolve cases by id, so
# an ad-hoc transcript has to become addressable. Each one gets a unique id rather than
# sharing a single "adhoc" slot, so concurrent requests cannot overwrite each other.
_RUNTIME: dict[str, dict[str, Any]] = {}


def register_runtime_case(case: dict[str, Any]) -> dict[str, Any]:
    """Make a caller-supplied case resolvable by the tools. Returns the case."""
    _RUNTIME[case["id"]] = case
    return case


def forget_runtime_case(case_id: str) -> None:
    _RUNTIME.pop(case_id, None)


def all_cases() -> list[dict[str, Any]]:
    """Every fixture case, in fixture order."""
    return CASES


def get_case(case_id: str) -> dict[str, Any]:
    """Look up a case by id, fixture or runtime. Raises KeyError with the known ids."""
    if case_id in _RUNTIME:
        return _RUNTIME[case_id]
    try:
        return _BY_ID[case_id]
    except KeyError as exc:
        raise KeyError(
            f"unknown case {case_id!r}; known ids: {', '.join(sorted(_BY_ID))}"
        ) from exc


def case_count() -> int:
    return len(CASES)


__all__ = [
    "CASES",
    "all_cases",
    "case_count",
    "forget_runtime_case",
    "get_case",
    "register_runtime_case",
]
