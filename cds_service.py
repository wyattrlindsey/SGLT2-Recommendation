"""Phase 4: a CDS Hooks service for ADA Standards of Care 2026, rec. 11.7a.

Wraps the Phase 3 rule (guideline.py) in the two endpoints the CDS Hooks
spec asks for:

  GET  /cds-services                 discovery: what this service does and
                                     what data it wants prefetched
  POST /cds-services/sglt2-t2d-ckd   the hook call: patient context in,
                                     suggestion cards out

and, for the Phase 5 SMART evidence app:

  POST /evidence/sglt2-t2d-ckd       the full evaluate() result for a prefetch

Run it:
  python -m uvicorn cds_service:app --reload --port 8000

Prefetch and the fallback fetch both produce (patient, resources): a Patient
dict and one flat list of Condition, Observation, MedicationRequest and
Medication dicts. to_patient_data() sorts that list into the arguments
guideline.build_patient_data() already takes, so Phase 3 code is unchanged.
evaluate_prefetch() is the one path from prefetch to a Result; the hook and
/evidence both use it, so the card and the app can't disagree.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

import requests
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

import guideline
import valuesets

log = logging.getLogger("cds_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

SERVICE_ID = "sglt2-t2d-ckd"
GUIDELINE_LABEL = "ADA Standards of Care 2026, rec. 11.7a"
GUIDELINE_URL = (
    "https://diabetesjournals.org/care/article/49/Supplement_1/S246/163914/"
    "11-Chronic-Kidney-Disease-and-Risk-Management"
)

# C6: criteria.md has not had clinician sign-off (Phase 3, Step 7 deferred).
# While this is False every card says so, and no card offers an order.
CLINICIAN_REVIEWED = False

SUMMARY_MAX = 140  # CDS Hooks: card summary must be under 140 characters
FETCH_TIMEOUT = 10  # seconds, for the fallback FHIR queries


def _token_list(concept: dict[str, set[str]]) -> str:
    """Turn a value set {system: {codes}} into a FHIR token list: sys|a,sys|b."""
    return ",".join(f"{system}|{code}" for system, codes in concept.items() for code in sorted(codes))


# Each prefetch key maps to one FHIR query. Together they must return
# everything guideline.fetch_patient_data() fetched in Phase 3.
PREFETCH: dict[str, str] = {
    "patient": "Patient/{{context.patientId}}",
    "conditions": "Condition?patient={{context.patientId}}&_count=500",
    "egfr": "Observation?patient={{context.patientId}}&code=" + _token_list(valuesets.EGFR) + "&_count=500",
    "uacr": "Observation?patient={{context.patientId}}&code=" + _token_list(valuesets.UACR) + "&_count=500",
    "meds": (
        "MedicationRequest?patient={{context.patientId}}&status=active"
        "&_include=MedicationRequest:medication&_count=200"
    ),
}

app = FastAPI(title="SGLT2 inhibitor CDS service (prototype)")

# The CDS Hooks sandbox calls the service from the browser, so CORS must be open.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------- discovery


@app.get("/cds-services")
def discovery() -> dict[str, Any]:
    return {
        "services": [
            {
                "hook": "patient-view",
                "id": SERVICE_ID,
                "title": "SGLT2 inhibitor for type 2 diabetes with CKD",
                "description": (
                    "Suggests an SGLT2 inhibitor with demonstrated kidney benefit for adults "
                    "with type 2 diabetes and CKD and eGFR >= 20 (" + GUIDELINE_LABEL + "). Prototype."
                ),
                "prefetch": PREFETCH,
            }
        ]
    }


# ---------------------------------------------------------------- data in


def _bundle_resources(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    return [e["resource"] for e in bundle.get("entry", []) if "resource" in e]


def _has_next(bundle: dict[str, Any]) -> bool:
    return any(link.get("relation") == "next" for link in bundle.get("link", []))


def from_prefetch(prefetch: dict[str, Any] | None) -> tuple[dict, list[dict]] | None:
    """Return (patient, resources) from prefetch, or None if anything is missing.

    C1: a missing key, a null value, or a search Bundle with a `next` page all
    count as incomplete. Deciding on a partial eGFR history could flip D5 or D7.
    """
    if not prefetch:
        return None
    patient = prefetch.get("patient")
    if not patient or patient.get("resourceType") != "Patient":
        return None
    resources: list[dict] = []
    for key in PREFETCH:
        if key == "patient":
            continue
        bundle = prefetch.get(key)
        if not bundle or bundle.get("resourceType") != "Bundle" or _has_next(bundle):
            return None
        resources.extend(_bundle_resources(bundle))
    return patient, resources


def from_fhir_server(base: str, patient_id: str, token: str | None) -> tuple[dict, list[dict]]:
    """Run the PREFETCH queries ourselves against the EHR's FHIR server."""
    session = requests.Session()
    session.headers["Accept"] = "application/fhir+json"
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    base = base.rstrip("/")

    def get(url: str) -> dict:
        r = session.get(url, timeout=FETCH_TIMEOUT)
        r.raise_for_status()
        return r.json()

    patient: dict = {}
    resources: list[dict] = []
    for key, template in PREFETCH.items():
        url = f"{base}/{template.replace('{{context.patientId}}', patient_id)}"
        if key == "patient":
            patient = get(url)
            continue
        while url:  # follow paging, as in Phase 1's Fhir.search()
            bundle = get(url)
            resources.extend(_bundle_resources(bundle))
            url = next((l["url"] for l in bundle.get("link", []) if l.get("relation") == "next"), None)
    return patient, resources


def to_patient_data(patient: dict, resources: list[dict]) -> guideline.PatientData:
    """Sort the flat resource list into guideline.build_patient_data()'s arguments.

    Other resource types (e.g. an OperationOutcome a server adds to a search
    Bundle) are ignored. Medications are keyed "Medication/<id>", the way a
    MedicationRequest's medicationReference points at them.
    """
    by_type: dict[str, list[dict]] = {}
    for r in resources:
        by_type.setdefault(r.get("resourceType", ""), []).append(r)
    medications = {f"Medication/{m['id']}": m for m in by_type.get("Medication", []) if m.get("id")}
    return guideline.build_patient_data(
        patient,
        by_type.get("Condition", []),
        by_type.get("Observation", []),
        by_type.get("MedicationRequest", []),
        medications,
    )


# ---------------------------------------------------------------- evaluation


def as_of() -> date:
    """The date every evaluation uses. Change it here only (Phase 4, Step 4)."""
    return date.today()


def evaluate_resources(patient: dict, resources: list[dict]) -> guideline.Result:
    """Layers 2 and 3: a Patient and its resources in, a guideline Result out."""
    return guideline.evaluate(to_patient_data(patient, resources), as_of())


def evaluate_prefetch(prefetch: dict[str, Any] | None) -> guideline.Result | None:
    """Return a Result, or None if prefetch is incomplete (C1).

    Used by the hook handler AND /evidence, so they can't disagree.
    """
    data_in = from_prefetch(prefetch)
    if data_in is None:
        return None
    return evaluate_resources(*data_in)


# ---------------------------------------------------------------- cards out


def _status(result: Any) -> str:
    status = getattr(result, "status", None)
    return str(getattr(status, "value", status)).upper()


def _short(text: str) -> str:
    return text if len(text) < SUMMARY_MAX else text[: SUMMARY_MAX - 2] + "…"


def _detail(reasons: list[str], intro: str) -> str:
    lines = [intro, ""] + [f"- {r}" for r in reasons]
    if not CLINICIAN_REVIEWED:
        lines += ["", "_Prototype on synthetic data. Rule logic has not yet been reviewed by a clinician._"]
    return "\n".join(lines)


def _card(summary: str, detail: str, indicator: str) -> dict[str, Any]:
    return {
        "uuid": str(uuid.uuid4()),
        "summary": _short(summary),
        "detail": detail,
        "indicator": indicator,
        "source": {"label": GUIDELINE_LABEL, "url": GUIDELINE_URL},
        "links": [{"label": "ADA Standards of Care, Section 11", "url": GUIDELINE_URL, "type": "absolute"}],
    }


def cards_for(result: Any) -> list[dict[str, Any]]:
    """C2-C4: one card for RECOMMEND, one for INSUFFICIENT_DATA, silence otherwise."""
    status = _status(result)
    reasons = list(getattr(result, "reasons", []) or [])
    if status == "RECOMMEND":
        return [
            _card(
                "Consider an SGLT2 inhibitor: type 2 diabetes with CKD, eGFR ≥ 20 (ADA 11.7a)",
                _detail(reasons, "This patient meets ADA 11.7a for starting an SGLT2 inhibitor:"),
                "info",
            )
        ]
    if status == "INSUFFICIENT_DATA":
        # evaluate() lists the evidence found first and what's missing last.
        missing = reasons[-1] if reasons else "missing data"
        return [
            _card(
                f"SGLT2 inhibitor eligibility unclear: {missing}",
                _detail(reasons, "ADA 11.7a may apply, but the record is missing what the rule needs:"),
                "info",
            )
        ]
    return []


# ---------------------------------------------------------------- the hook


@app.post("/cds-services/{service_id}")
async def call_service(service_id: str, request: Request) -> dict[str, Any]:
    if service_id != SERVICE_ID:
        raise HTTPException(status_code=404, detail=f"Unknown service {service_id}")
    body = await request.json()
    hook_instance = body.get("hookInstance", "?")
    patient_id = (body.get("context") or {}).get("patientId")
    if body.get("hook") != "patient-view" or not patient_id:
        raise HTTPException(status_code=400, detail="Expected a patient-view hook with context.patientId")

    # C5: the spec says verify the EHR's JWT. The prototype only notes it (exercise).
    log.info("hook=%s instance=%s jwt=%s", body.get("hook"), hook_instance,
             "present" if request.headers.get("authorization") else "absent")

    try:
        result = evaluate_prefetch(body.get("prefetch"))
        source = "prefetch"
        if result is None:  # C1: incomplete prefetch, fetch it ourselves
            base = body.get("fhirServer")
            if not base:
                log.warning("instance=%s incomplete prefetch and no fhirServer", hook_instance)
                return {"cards": []}
            token = (body.get("fhirAuthorization") or {}).get("access_token")
            patient, resources = await run_in_threadpool(from_fhir_server, base, patient_id, token)
            result = evaluate_resources(patient, resources)
            source = "fhirServer"
    except Exception:  # C7: never break the EHR screen; log and stay silent
        log.exception("instance=%s evaluation failed", hook_instance)
        return {"cards": []}

    # Log the status, not the reasons: reasons contain clinical details.
    log.info("instance=%s source=%s status=%s", hook_instance, source, _status(result))
    return {"cards": cards_for(result)}


# ---------------------------------------------------------------- evidence (Phase 5)


@app.post("/evidence/{service_id}")
def evidence(service_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """The full evaluate() result for the SMART evidence app (S3, S4, S7).

    Takes the same prefetch dict as the hook, built by the app with its own
    token. No fhirServer fallback: if the data is incomplete, say so (422)
    rather than fetch more on the app's behalf. Lives outside /cds-services
    because that path belongs to the CDS Hooks spec.
    """
    if service_id != SERVICE_ID:
        raise HTTPException(status_code=404, detail=f"Unknown service {service_id}")
    try:
        result = evaluate_prefetch(body.get("prefetch"))
    except Exception:
        log.exception("evidence: evaluation failed")
        raise HTTPException(status_code=500, detail="Evaluation failed. See the service log.")
    if result is None:
        raise HTTPException(
            status_code=422,
            detail="Incomplete data: a query is missing or has more pages. Nothing was evaluated.",
        )
    # As with the hook: log the status, not the reasons.
    log.info("evidence: status=%s", _status(result))
    return {
        "status": _status(result),
        "reasons": list(result.reasons or []),
        "facts": jsonable_encoder(result.facts),
        "as_of": as_of().isoformat(),
        "reviewed": CLINICIAN_REVIEWED,
    }
