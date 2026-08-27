"""Build and verify backend/evaluation/corpus/gold_v1/manifest.json (schema v2).

64 reviewed cases across 11 categories. Every locator_match is verified against
the real extractor output before the manifest is written.
"""
# ruff: noqa: E501 -- query strings and CSS element selectors are data, not code
import sys
from pathlib import Path

# Allow running directly from the corpus directory: put the backend package
# root (the script's great-great-grandparent) on sys.path.
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.evaluation.datasets import (  # noqa: E402
    EvaluationCorpusDocument,
    load_executable_dataset,
    passage_matches_locator,
)
from app.ingestion.chunking import chunk_sections  # noqa: E402
from app.ingestion.extraction import extract_document  # noqa: E402
from app.ingestion.normalization import normalize_document  # noqa: E402

ROOT = Path(__file__).parent
DOCS = ROOT / "documents"
VERSION = "gold_v1"
SOURCE_PATH_PREFIX = f"evaluation/{VERSION}/documents"

MEDIA = {
    ".md": "text/markdown",
    ".py": "text/x-python",
    ".ts": "text/typescript",
    ".html": "text/html",
    ".pdf": "application/pdf",
}

# ---------------------------------------------------------------- passages

def hp(*path: str) -> dict:
    return {"heading_path": list(path)}


def html_element(selector: str) -> dict:
    return {"element": selector}


BASE_HTML = "html:nth-of-type(1) > body:nth-of-type(1) > main:nth-of-type(1)"

PASSAGES = {
    "ops-catalog": [
        ("ops-service-registry", hp("Operations catalog", "Service registry")),
        ("ops-qpx731", hp("Operations catalog", "Queue poison error")),
        ("ops-release-windows", hp("Operations catalog", "Release windows")),
        ("ops-p1-escalation", hp("Operations catalog", "Priority-one escalation")),
        ("ops-batch-ledger", hp("Operations catalog", "Batch ledger")),
        ("ops-status-probes", hp("Operations catalog", "Status probes")),
    ],
    "people-policy": [
        ("hr-leave-carryover", hp("People policy", "Annual leave carry-over")),
        ("hr-remote-equipment", hp("People policy", "Remote equipment")),
        ("hr-new-starter", hp("People policy", "New starter sequence")),
        ("hr-learning-allowance", hp("People policy", "Learning allowance")),
        ("hr-contractor-access", hp("People policy", "Contractor access")),
        ("hr-expense-submission", hp("People policy", "Expense submission")),
    ],
    "retention-current": [
        ("ret-current-audit", hp("Current records schedule", "Customer audit events")),
        ("ret-current-billing", hp("Current records schedule", "Billing exports")),
        ("ret-current-deletion", hp("Current records schedule", "Deletion queue")),
    ],
    "retention-legacy": [
        ("ret-legacy-audit", hp("Superseded records schedule", "Customer audit events")),
        ("ret-legacy-billing", hp("Superseded records schedule", "Billing exports")),
        ("ret-legacy-deletion", hp("Superseded records schedule", "Deletion queue")),
    ],
    "security-standard": [
        ("sec-session", hp("Security standard", "Session controls")),
        ("sec-417", hp("Security standard", "Incident code SEC-417")),
        ("sec-backup-encryption", hp("Security standard", "Backup encryption")),
        ("sec-breakglass", hp("Security standard", "Break-glass access")),
        ("sec-log-retention", hp("Security standard", "Security log retention")),
        ("sec-phishing", hp("Security standard", "Phishing reports")),
    ],
    "support-eu": [
        ("eu-service-hours", hp("Europe support", "Service hours")),
        ("eu-urgent-route", hp("Europe support", "Urgent route")),
    ],
    "support-sg": [
        ("sg-service-hours", hp("Singapore support", "Service hours")),
        ("sg-urgent-route", hp("Singapore support", "Urgent route")),
    ],
    "travel-contractors": [
        ("trv-contractor-approval", hp("Contractor travel policy", "Trip approval")),
        ("trv-contractor-lodging", hp("Contractor travel policy", "Lodging cap")),
        ("trv-contractor-deadline", hp("Contractor travel policy", "Expense deadline")),
    ],
    "travel-employees": [
        ("trv-employee-domestic", hp("Employee travel policy", "Domestic approval")),
        ("trv-employee-international", hp("Employee travel policy", "International approval")),
        ("trv-employee-ground", hp("Employee travel policy", "Ground transport")),
    ],
    "contractor-faq": [
        ("faq-remote-equipment", hp("Contractor travel FAQ", "Remote equipment")),
        ("faq-lodging-cap", hp("Contractor travel FAQ", "Lodging cap")),
        ("faq-trip-approval", hp("Contractor travel FAQ", "Trip approval")),
        ("faq-expense-deadline", hp("Contractor travel FAQ", "Expense deadline")),
    ],
    "scheduler": [
        ("scheduler-compute-backoff", {"symbol": "compute_backoff"}),
        ("scheduler-should-retry", {"symbol": "should_retry"}),
        ("scheduler-parse-window", {"symbol": "parse_window"}),
        ("scheduler-lease-guard", {"symbol": "LeaseGuard"}),
        ("scheduler-normalize-job-id", {"symbol": "normalize_job_id"}),
    ],
    "throttle": [
        ("throttle-normalize-key", {"symbol": "normalizeKey"}),
        ("throttle-token-bucket", {"symbol": "TokenBucket"}),
        ("throttle-retry-after-ms", {"symbol": "retryAfterMs"}),
        ("throttle-circuit-breaker", {"symbol": "CircuitBreaker"}),
        ("throttle-partition-for", {"symbol": "partitionFor"}),
        ("throttle-partition-total", {"symbol": "total"}),
    ],
    "portal-guide": [
        ("portal-account-setup-li1", html_element(f"{BASE_HTML} > #account-setup > ul:nth-of-type(1) > li:nth-of-type(1)")),
        ("portal-account-setup-li2", html_element(f"{BASE_HTML} > #account-setup > ul:nth-of-type(1) > li:nth-of-type(2)")),
        ("portal-account-setup-li3", html_element(f"{BASE_HTML} > #account-setup > ul:nth-of-type(1) > li:nth-of-type(3)")),
        ("portal-invoice-formats", html_element(f"{BASE_HTML} > #invoice-export > p:nth-of-type(1)")),
        ("portal-invoice-limit", html_element(f"{BASE_HTML} > #invoice-export > p:nth-of-type(2)")),
        ("portal-alert-p1", html_element(f"{BASE_HTML} > #alert-routing > ul:nth-of-type(1) > li:nth-of-type(1)")),
        ("portal-alert-p2", html_element(f"{BASE_HTML} > #alert-routing > ul:nth-of-type(1) > li:nth-of-type(2)")),
        ("portal-alert-p3", html_element(f"{BASE_HTML} > #alert-routing > ul:nth-of-type(1) > li:nth-of-type(3)")),
        ("portal-search-syntax", html_element(f"{BASE_HTML} > #search-syntax > p:nth-of-type(1)")),
        ("portal-shortcut-home", html_element(f"{BASE_HTML} > #keyboard-shortcuts > ul:nth-of-type(1) > li:nth-of-type(1)")),
        ("portal-shortcut-help", html_element(f"{BASE_HTML} > #keyboard-shortcuts > ul:nth-of-type(1) > li:nth-of-type(2)")),
        ("portal-status-badges", html_element(f"{BASE_HTML} > #status-badges > p:nth-of-type(1)")),
    ],
    "resilience-handbook": [
        ("resilience-p1", {"page": 1}),
        ("resilience-p2", {"page": 2}),
        ("resilience-p3", {"page": 3}),
        ("resilience-p4", {"page": 4}),
    ],
}

DOC_TAGS = {
    "ops-catalog": ["operations"],
    "people-policy": ["policy", "hr"],
    "retention-current": ["records", "policy"],
    "retention-legacy": ["records", "policy"],
    "security-standard": ["policy", "security"],
    "support-eu": ["operations", "support"],
    "support-sg": ["operations", "support"],
    "travel-contractors": ["policy", "travel"],
    "travel-employees": ["policy", "travel"],
    "contractor-faq": ["policy", "travel"],
    "scheduler": ["code", "python"],
    "throttle": ["code", "typescript"],
    "portal-guide": ["operations", "portal"],
    "resilience-handbook": ["operations", "resilience"],
}

SOURCE_URI = {"portal-guide": "https://docs.example.invalid/portal/operations"}

# ---------------------------------------------------------------- cases

CASES = [
    # exact identifiers, numbers, and error codes
    ("e-svc1042", "exact_identifier", "What is the service identifier of the Atlas gateway?", ["ops-service-registry"], ["ops-service-registry"], [], ["SVC-1042"]),
    ("e-qpx731", "exact_identifier", "What does error code QPX-731 mean?", ["ops-qpx731"], ["ops-qpx731"], [], ["QPX-731", "quarantine"]),
    ("e-fin882", "exact_identifier", "What is the identifier of the finance reconciliation job?", ["ops-batch-ledger"], ["ops-batch-ledger"], [], ["FIN-882"]),
    ("e-sec417", "exact_identifier", "Which incident code requires rotating affected service credentials within thirty minutes?", ["sec-417"], ["sec-417"], [], ["SEC-417", "thirty minutes"]),
    ("e-eu7", "exact_identifier", "What is the route code for urgent Europe support requests?", ["eu-urgent-route"], ["eu-urgent-route"], [], ["EU-7"]),
    ("e-sg2", "exact_identifier", "What is the route code for urgent Singapore support requests?", ["sg-urgent-route"], ["sg-urgent-route"], [], ["SG-2"]),
    ("e-learning-sgd", "exact_identifier", "What is the annual employee learning allowance?", ["hr-learning-allowance"], ["hr-learning-allowance"], [], ["SGD 1,200"]),
    ("e-remote-threshold", "exact_identifier", "Above what purchase price does remote-work equipment require approval?", ["hr-remote-equipment"], ["hr-remote-equipment"], [], ["SGD 1,800"]),
    # semantic paraphrases
    ("s-equipment-signoff", "semantic_paraphrase", "Who has to give the green light before an employee buys pricey work-from-home gear?", ["hr-remote-equipment"], ["hr-remote-equipment"], [], ["department head", "Information Technology"]),
    ("s-idle-lock", "semantic_paraphrase", "If an administrator steps away from the keyboard, when does the session get locked?", ["sec-session"], ["sec-session"], [], ["twenty minutes"]),
    ("s-phish-act", "semantic_paraphrase", "Someone suspects an email is phishing. How quickly must they act and with which action?", ["sec-phishing"], ["sec-phishing"], [], ["fifteen minutes", "Report Phishing"]),
    ("s-leave-rollover", "semantic_paraphrase", "What happens to unused annual leave above the carry-over cap?", ["hr-leave-carryover"], ["hr-leave-carryover"], [], ["five days", "31 December"]),
    ("s-release-freeze", "semantic_paraphrase", "A team wants a routine release shortly before a quarter ends. What must happen first?", ["ops-release-windows"], ["ops-release-windows"], [], ["five business days", "freeze"]),
    ("s-forward-phish", "semantic_paraphrase", "Is forwarding a suspected phishing email to a colleague for review acceptable?", ["sec-phishing"], ["sec-phishing"], [], ["prohibited"]),
    ("s-qpx-operator", "semantic_paraphrase", "What must an operator do with a message flagged by error code QPX-731?", ["ops-qpx731"], ["ops-qpx731"], [], ["quarantine", "SHA-256"]),
    # cross-page (PDF multi-page evidence)
    ("xp-rpo-approvers", "cross_page", "What is the recovery point objective, and which two people must approve the replica promotion?", ["resilience-p2", "resilience-p3"], ["resilience-p2", "resilience-p3"], [2, 3], ["ten minutes", "Reliability", "product lead"]),
    ("xp-scope-rto", "cross_page", "Which services are in scope, and what is the recovery time objective?", ["resilience-p1", "resilience-p2"], ["resilience-p1", "resilience-p2"], [1, 2], ["authentication", "checkout", "recovery time objective"]),
    ("xp-integrity-freeze", "cross_page", "Who validates checkout integrity before traffic returns, and what is the first failover action?", ["resilience-p1", "resilience-p3"], ["resilience-p1", "resilience-p3"], [1, 3], ["Commerce lead", "freeze writes"]),
    ("xp-rpo-corrective", "cross_page", "What is the recovery point objective, and how quickly must corrective actions be assigned after an exercise?", ["resilience-p2", "resilience-p4"], ["resilience-p2", "resilience-p4"], [2, 4], ["ten minutes", "ten business days"]),
    ("xp-scope-exercise", "cross_page", "Which services does the handbook cover, and how often is a recovery exercise run?", ["resilience-p1", "resilience-p4"], ["resilience-p1", "resilience-p4"], [1, 4], ["authentication", "checkout", "quarter"]),
    ("xp-approvers-evidence", "cross_page", "Who must confirm the replica promotion, and when must exercise evidence be archived?", ["resilience-p3", "resilience-p4"], ["resilience-p3", "resilience-p4"], [3, 4], ["Reliability", "thirty days"]),
    # cross-section (within one document)
    ("ms-417-backup", "multi_section", "Which incident code requires revoking sessions, and how often must backup encryption keys rotate?", ["sec-417", "sec-backup-encryption"], ["sec-417", "sec-backup-encryption"], [], ["SEC-417", "quarter"]),
    ("ms-equipment-learning", "multi_section", "What approval does remote equipment need, and what is the annual learning allowance?", ["hr-remote-equipment", "hr-learning-allowance"], ["hr-remote-equipment", "hr-learning-allowance"], [], ["department head", "SGD 1,200"]),
    ("ms-svc-qpx", "multi_section", "What service identifier does the Atlas gateway use, and what does QPX-731 mean?", ["ops-service-registry", "ops-qpx731"], ["ops-service-registry", "ops-qpx731"], [], ["SVC-1042", "QPX-731"]),
    ("ms-domestic-flights", "multi_section", "What approval is needed for domestic employee travel above SGD 750, and how far ahead should international flights be booked?", ["trv-employee-domestic", "trv-employee-international"], ["trv-employee-domestic", "trv-employee-international"], [], ["Finance", "fourteen days"]),
    ("ms-logs-breakglass", "multi_section", "How long are security logs retained, and what is the break-glass access expiry?", ["sec-log-retention", "sec-breakglass"], ["sec-log-retention", "sec-breakglass"], [], ["four hundred days", "four hours"]),
    # code symbol / behavior
    ("c-backoff-attempt6", "code_symbol", "What does compute_backoff return for attempt 6?", ["scheduler-compute-backoff"], ["scheduler-compute-backoff"], [], ["120"]),
    ("c-retry-codes", "code_symbol", "Which HTTP status codes does should_retry treat as retryable?", ["scheduler-should-retry"], ["scheduler-should-retry"], [], ["408", "429"]),
    ("c-window-over6", "code_symbol", "What does parse_window raise for a maintenance window longer than six hours?", ["scheduler-parse-window"], ["scheduler-parse-window"], [], ["six hours"]),
    ("c-lease-renew", "code_symbol", "What happens when a non-owner calls renew on a LeaseGuard?", ["scheduler-lease-guard"], ["scheduler-lease-guard"], [], ["PermissionError", "lease owner"]),
    ("c-normalize-slashes", "code_symbol", "What does normalizeKey do with slashes in the input?", ["throttle-normalize-key"], ["throttle-normalize-key"], [], ["toLowerCase", "replaceAll"]),
    ("c-bucket-empty", "code_symbol", "What does TokenBucket.consume return when fewer than one token is available?", ["throttle-token-bucket"], ["throttle-token-bucket"], [], ["false"]),
    ("c-breaker-threshold", "code_symbol", "After how many consecutive failures does CircuitBreaker open the circuit?", ["throttle-circuit-breaker"], ["throttle-circuit-breaker"], [], ["5", "30"]),
    ("c-partition-zero", "code_symbol", "What does partitionFor do when shardCount is zero?", ["throttle-partition-for"], ["throttle-partition-for"], [], ["shard count must be positive"]),
    # HTML heading and list questions
    ("h-invoice-rows", "html_heading_list", "What is the maximum number of rows in a single portal invoice export?", ["portal-invoice-limit"], ["portal-invoice-limit"], [], ["25,000"]),
    ("h-invoice-formats", "html_heading_list", "Which formats does the portal invoice export support?", ["portal-invoice-formats"], ["portal-invoice-formats"], [], ["CSV", "JSON"]),
    ("h-alert-p1", "html_heading_list", "How are priority one portal alerts delivered?", ["portal-alert-p1"], ["portal-alert-p1"], [], ["SMS", "phone call"]),
    ("h-alert-email", "html_heading_list", "Which portal alert priorities are delivered by email?", ["portal-alert-p2", "portal-alert-p3"], ["portal-alert-p2", "portal-alert-p3"], [], ["email"]),
    ("h-setup-checklist", "html_heading_list", "What are the three items on the portal account setup checklist?", ["portal-account-setup-li1", "portal-account-setup-li2", "portal-account-setup-li3"], ["portal-account-setup-li1", "portal-account-setup-li2", "portal-account-setup-li3"], [], ["passkey", "recovery codes"]),
    ("h-search-syntax", "html_heading_list", "How does the portal search for an exact phrase?", ["portal-search-syntax"], ["portal-search-syntax"], [], ["quotation marks"]),
    ("h-shortcut-home", "html_heading_list", "What keyboard shortcut returns to the portal home page?", ["portal-shortcut-home"], ["portal-shortcut-home"], [], ["g then h"]),
    # multi-Document comparisons
    ("md-audit-windows", "multi_document", "How does the current records schedule change customer audit event retention compared with the superseded schedule?", ["ret-current-audit", "ret-legacy-audit"], ["ret-current-audit", "ret-legacy-audit"], [], ["365 days", "180 days"]),
    ("md-deletion-windows", "multi_document", "Compare the recovery queue windows for deletion requests under the current and superseded schedules.", ["ret-current-deletion", "ret-legacy-deletion"], ["ret-current-deletion", "ret-legacy-deletion"], [], ["thirty days", "sixty days"]),
    ("md-billing-windows", "multi_document", "How do billing export retention periods compare between the current and superseded schedules?", ["ret-current-billing", "ret-legacy-billing"], ["ret-current-billing", "ret-legacy-billing"], [], ["seven years", "five years"]),
    ("md-support-hours", "multi_document", "How do the Europe and Singapore support desks differ in service hours?", ["eu-service-hours", "sg-service-hours"], ["eu-service-hours", "sg-service-hours"], [], ["Monday through Friday", "Monday through Saturday"]),
    ("md-urgent-codes", "multi_document", "Which support region has the faster initial-response target, and what are the two route codes?", ["eu-urgent-route", "sg-urgent-route"], ["eu-urgent-route", "sg-urgent-route"], [], ["EU-7", "SG-2", "twelve-minute"]),
    ("md-approval-employee", "multi_document", "How do approval rules differ between domestic and international employee travel?", ["trv-employee-domestic", "trv-employee-international"], ["trv-employee-domestic", "trv-employee-international"], [], ["manager", "regional director"]),
    ("md-access-grants", "multi_document", "How do contractor access grants compare with break-glass emergency access?", ["hr-contractor-access", "sec-breakglass"], ["hr-contractor-access", "sec-breakglass"], [], ["eight hours", "four hours"]),
    # metadata-filtered questions
    ("m-billing-current", "metadata_filtered", "How long does the current records schedule keep billing exports?", ["ret-current-billing"], ["ret-current-billing"], [], ["seven years"], {"document_ids": ["retention-current"]}),
    ("m-deletion-current", "metadata_filtered", "How long do approved deletion requests remain recoverable under the current records schedule?", ["ret-current-deletion"], ["ret-current-deletion"], [], ["thirty days"], {"document_ids": ["retention-current"]}),
    ("m-sg-hours", "metadata_filtered", "What are the Singapore support desk hours?", ["sg-service-hours"], ["sg-service-hours"], [], ["Monday through Saturday", "Singapore Time"], {"document_ids": ["support-sg"], "source_kinds": ["markdown"]}),
    ("m-python-lease", "metadata_filtered", "What does the Python scheduler's LeaseGuard renew raise for a non-owner?", ["scheduler-lease-guard"], ["scheduler-lease-guard"], [], ["PermissionError"], {"languages": ["python"]}),
    ("m-security-session", "metadata_filtered", "After how many idle minutes does an administrative session lock?", ["sec-session"], ["sec-session"], [], ["twenty minutes"], {"tags": ["security"]}),
    # broad questions
    ("b-security-overview", "broad_summary", "Summarize the key controls described in the security standard.", ["sec-session", "sec-417", "sec-backup-encryption", "sec-breakglass", "sec-log-retention", "sec-phishing"], ["sec-417", "sec-backup-encryption"], [], ["SEC-417", "AES-256"]),
    ("b-travel-overview", "broad_summary", "What travel policies apply to both employees and contractors?", ["trv-employee-domestic", "trv-employee-international", "trv-employee-ground", "trv-contractor-approval", "trv-contractor-lodging", "trv-contractor-deadline"], ["trv-employee-domestic", "trv-contractor-approval"], [], ["Procurement", "manager"]),
    ("b-ops-overview", "broad_summary", "What does the operations catalog document?", ["ops-service-registry", "ops-qpx731", "ops-release-windows", "ops-p1-escalation", "ops-batch-ledger", "ops-status-probes"], ["ops-service-registry", "ops-qpx731", "ops-status-probes"], [], ["SVC-1042", "QPX-731"]),
    # unsupported
    ("u-office-temp", "unsupported", "What is the office temperature policy?", [], [], [], [], False),
    ("u-ceo-salary", "unsupported", "What is the CEO's annual salary?", [], [], [], [], False),
    ("u-all-hands", "unsupported", "Which day is the company all-hands meeting held?", [], [], [], [], False),
    ("u-support-phone", "unsupported", "What is the customer support phone number for the portal?", [], [], [], [], False),
    # conflicting evidence
    ("cf-lodging-cap", "conflicting_evidence", "What is the nightly contractor lodging cap?", ["trv-contractor-lodging", "faq-lodging-cap"], ["trv-contractor-lodging", "faq-lodging-cap"], [], ["cannot answer reliably"], False),
    ("cf-trip-approval", "conflicting_evidence", "Can contractors approve their own trips?", ["trv-contractor-approval", "faq-trip-approval"], ["trv-contractor-approval", "faq-trip-approval"], [], ["cannot answer reliably"], False),
    ("cf-expense-deadline", "conflicting_evidence", "How long do contractors have to submit travel expenses?", ["trv-contractor-deadline", "faq-expense-deadline"], ["trv-contractor-deadline", "faq-expense-deadline"], [], ["cannot answer reliably"], False),
    ("cf-remote-equipment", "conflicting_evidence", "Above what price does remote-work equipment require approval?", ["hr-remote-equipment", "faq-remote-equipment"], ["hr-remote-equipment", "faq-remote-equipment"], [], ["cannot answer reliably"], False),
]

assert len(CASES) == 64, f"expected 64 cases, got {len(CASES)}"

# ---------------------------------------------------------------- manifest

def build_manifest() -> dict:
    documents = []
    for document_id, passages in PASSAGES.items():
        relative = f"documents/{_filename_for(document_id)}"
        document = {
            "document_id": document_id,
            "path": relative,
            "tags": DOC_TAGS[document_id],
            "passages": [
                {
                    "passage_id": passage_id,
                    "locator_id": f"{_kind(document_id)}:{document_id}#{passage_id}",
                    "locator_match": match,
                }
                for passage_id, match in passages
            ],
        }
        if document_id in SOURCE_URI:
            document["source_uri"] = SOURCE_URI[document_id]
        documents.append(document)

    splits = _assign_splits(CASES)
    cases = []
    for entry in CASES:
        case_id, category, query, relevant, required, pages, terms, *rest = entry
        supported = True
        filters = None
        if rest:
            if len(rest) == 1 and isinstance(rest[0], bool):
                supported = rest[0]
            else:
                filters = rest[0]
                if len(rest) == 2:
                    supported = rest[1]
        relevance_grades = {passage_id: 3 for passage_id in required}
        relevance_grades.update(
            {
                passage_id: 1
                for passage_id in relevant
                if passage_id not in relevance_grades
            }
        )
        case = {
            "case_id": case_id,
            "category": category,
            "query": query,
            "relevant_passage_ids": relevant,
            "required_passage_ids": required,
            "expected_citation_pages": pages,
            "expected_answer_terms": terms,
            "expects_supported_answer": supported,
            "reviewed": True,
            "split": splits[case_id],
            "relevance_grades": relevance_grades,
        }
        if filters:
            case["filters"] = filters
        cases.append(case)

    return {
        "schema_version": 2,
        "version": VERSION,
        "description": (
            "Redistributable gold evaluation set: 64 reviewed questions over "
            "heterogeneous synthetic Markdown, HTML, PDF, Python, and TypeScript "
            "sources. Includes unsupported and genuinely conflicting-evidence cases."
        ),
        "license": "CC0-1.0",
        "license_file": "LICENSE.txt",
        "provenance": "synthetic",
        "contains_sensitive_data": False,
        "review_status": "reviewed",
        "headline_eligible": False,
        "documents": documents,
        "cases": cases,
    }


def _assign_splits(cases: list[tuple]) -> dict[str, str]:
    """Assign stable, category-stratified train/dev/test splits.

    Cases are grouped by category and assigned by sorted case_id with a fixed
    pattern, so regenerating the manifest never changes which cases are held
    out as test. Every category with three or more cases contributes a test
    case, keeping the untouched test split representative.
    """
    by_category: dict[str, list[str]] = {}
    for case_id, category, *_ in cases:
        by_category.setdefault(category, []).append(case_id)
    result: dict[str, str] = {}
    for category in sorted(by_category):
        ordered = sorted(by_category[category])
        count = len(ordered)
        if count == 1:
            pattern = ("train",)
        elif count == 2:
            pattern = ("train", "dev")
        elif count == 3:
            pattern = ("train", "dev", "test")
        else:
            pattern = ("train", "train", "dev", "test")
        for index, case_id in enumerate(ordered):
            result[case_id] = pattern[index % len(pattern)]
    return result


def _filename_for(document_id: str) -> str:
    return {
        "ops-catalog": "operations-catalog.md",
        "people-policy": "people-policy.md",
        "retention-current": "retention-current.md",
        "retention-legacy": "retention-legacy.md",
        "security-standard": "security-standard.md",
        "support-eu": "support-eu.md",
        "support-sg": "support-sg.md",
        "travel-contractors": "travel-contractors.md",
        "travel-employees": "travel-employees.md",
        "contractor-faq": "contractor-faq.md",
        "scheduler": "scheduler.py",
        "throttle": "throttle.ts",
        "portal-guide": "portal-guide.html",
        "resilience-handbook": "resilience-handbook.pdf",
    }[document_id]


def _kind(document_id: str) -> str:
    return {
        "scheduler": "code",
        "throttle": "code",
        "portal-guide": "html",
        "resilience-handbook": "pdf",
    }.get(document_id, "markdown")


# ---------------------------------------------------------------- verify

def real_chunk_locators(document: EvaluationCorpusDocument) -> list[dict]:
    media_type = MEDIA[document.path.suffix]
    extracted = extract_document(
        document.path,
        media_type,
        max_pages=500,
        source_path=f"{SOURCE_PATH_PREFIX}/{document.path.name}",
    )
    normalized = normalize_document(extracted)
    return [dict(chunk.locator) for chunk in chunk_sections(
        normalized.sections, target_characters=800, overlap_characters=100
    )]


def verify(dataset) -> None:
    for document in dataset.documents:
        locators = real_chunk_locators(document)
        matched: dict[str, list[int]] = {}
        for passage in document.passages:
            hits = [
                index for index, locator in enumerate(locators)
                if passage_matches_locator(passage, locator)
            ]
            assert len(hits) == 1, (
                f"{document.document_id} passage {passage.passage_id} "
                f"matched {len(hits)} chunks: {hits}"
            )
            matched[passage.passage_id] = hits
        for passage in document.passages:
            for other in document.passages:
                if other.passage_id == passage.passage_id:
                    continue
                assert matched[passage.passage_id] != matched[other.passage_id], (
                    f"{document.document_id}: {passage.passage_id} and "
                    f"{other.passage_id} resolve to the same chunk"
                )
    print(f"verified {sum(len(d.passages) for d in dataset.documents)} passages "
          f"resolve to exactly one chunk each")


def main() -> None:
    manifest = build_manifest()
    destination = ROOT / "manifest.json"
    destination.write_text(
        __import__("json").dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    dataset = load_executable_dataset(destination)
    verify(dataset)
    print(f"wrote {destination}")
    print(f"documents={len(dataset.documents)} cases={len(dataset.cases)}")
    categories = {}
    for case in dataset.cases:
        categories.setdefault(case.category, 0)
        categories[case.category] += 1
    for category in sorted(categories):
        print(f"  {category}: {categories[category]}")
    splits = {}
    for case in dataset.cases:
        splits.setdefault(case.split, 0)
        splits[case.split] += 1
    for split in ("train", "dev", "test"):
        print(f"  split {split}: {splits.get(split, 0)}")


if __name__ == "__main__":
    main()
