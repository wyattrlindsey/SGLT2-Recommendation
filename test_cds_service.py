"""Tests for cds_service.py. Run: python3 -m pytest -q test_cds_service.py

These use hand-built FHIR resources, so they exercise the real
guideline.build_patient_data() and evaluate() from Phase 3. No server needed.
"""
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import cds_service

client = TestClient(cds_service.app)
PID = "p1"
URL = f"/cds-services/{cds_service.SERVICE_ID}"


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def bundle(*resources, next_url=None):
    b = {"resourceType": "Bundle", "type": "searchset", "entry": [{"resource": r} for r in resources]}
    if next_url:
        b["link"] = [{"relation": "next", "url": next_url}]
    return b


def condition(snomed: str, display: str) -> dict:
    return {
        "resourceType": "Condition",
        "id": f"c-{snomed}",
        "subject": {"reference": f"Patient/{PID}"},
        "clinicalStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-clinical", "code": "active"}]},
        "verificationStatus": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/condition-ver-status", "code": "confirmed"}]},
        "code": {"coding": [{"system": "http://snomed.info/sct", "code": snomed, "display": display}]},
        "onsetDateTime": days_ago(2000),
    }


def egfr(value: float, ago: int) -> dict:
    return {
        "resourceType": "Observation",
        "id": f"egfr-{ago}",
        "status": "final",
        "subject": {"reference": f"Patient/{PID}"},
        "code": {"coding": [{"system": "http://loinc.org", "code": "33914-3"}]},
        "effectiveDateTime": days_ago(ago),
        "valueQuantity": {"value": value, "unit": "mL/min/{1.73_m2}"},
    }


PATIENT = {"resourceType": "Patient", "id": PID, "birthDate": "1960-01-01", "name": [{"given": ["Pat"], "family": "Test"}]}
T2D = condition("44054006", "Diabetes mellitus type 2")
CKD3 = condition("433144002", "Chronic kidney disease stage 3")


def request_body(prefetch=None, fhir_server=None):
    body = {
        "hook": "patient-view",
        "hookInstance": "test-instance",
        "context": {"userId": "Practitioner/1", "patientId": PID},
    }
    if prefetch is not None:
        body["prefetch"] = prefetch
    if fhir_server:
        body["fhirServer"] = fhir_server
    return body


def prefetch(conditions=(), egfrs=(), meds=()):
    return {
        "patient": PATIENT,
        "conditions": bundle(*conditions),
        "egfr": bundle(*egfrs),
        "uacr": bundle(),
        "meds": bundle(*meds),
    }


# Tests where an exception inside the service is the expected path. Everywhere
# else, an exception is a bug: C7 would turn it into a silent "no card", and a
# "should be silent" test would pass for the wrong reason.
ERRORS_EXPECTED = {"test_unreachable_fhir_server_is_silent_not_error"}


@pytest.fixture(autouse=True)
def no_hidden_errors(request, caplog):
    yield
    if request.node.originalname not in ERRORS_EXPECTED:
        failed = [r.getMessage() for r in caplog.records if "evaluation failed" in r.getMessage()]
        assert not failed, f"service raised and C7 hid it: {failed}"


def sglt2_order_by_reference():
    """An active canagliflozin order that names its drug via a Medication resource."""
    med = {
        "resourceType": "Medication",
        "id": "m1",
        "code": {"coding": [{"system": "http://www.nlm.nih.gov/research/umls/rxnorm",
                             "code": "1373463", "display": "canagliflozin 100 MG Oral Tablet"}]},
    }
    mr = {
        "resourceType": "MedicationRequest",
        "id": "mr1",
        "status": "active",
        "subject": {"reference": f"Patient/{PID}"},
        "medicationReference": {"reference": "Medication/m1"},
    }
    return mr, med


# ---------------------------------------------------------------- discovery


def test_discovery_lists_service_with_prefetch():
    services = client.get("/cds-services").json()["services"]
    assert len(services) == 1
    s = services[0]
    assert s["id"] == cds_service.SERVICE_ID and s["hook"] == "patient-view"
    assert set(s["prefetch"]) == {"patient", "conditions", "egfr", "uacr", "meds"}
    assert "33914-3" in s["prefetch"]["egfr"]


# ---------------------------------------------------------------- cards


def test_recommend_returns_one_valid_card():
    r = client.post(URL, json=request_body(prefetch([T2D, CKD3], [egfr(41, 30)])))
    cards = r.json()["cards"]
    assert r.status_code == 200 and len(cards) == 1
    card = cards[0]
    assert len(card["summary"]) < 140
    assert card["indicator"] in {"info", "warning", "critical"}
    assert card["source"]["label"]
    assert "SGLT2" in card["summary"]
    assert "suggestions" not in card  # C6: no orders until clinician sign-off


def test_not_applicable_is_silent():
    r = client.post(URL, json=request_body(prefetch([T2D], [egfr(95, 30)])))
    assert r.json() == {"cards": []}


def test_low_egfr_is_silent():
    r = client.post(URL, json=request_body(prefetch([T2D, CKD3], [egfr(15, 30)])))
    assert r.json() == {"cards": []}


def test_insufficient_data_card_when_no_recent_egfr():
    r = client.post(URL, json=request_body(prefetch([T2D, CKD3], [egfr(41, 800)])))
    cards = r.json()["cards"]
    assert len(cards) == 1 and "unclear" in cards[0]["summary"]
    assert "No eGFR in the last 365 days" in cards[0]["summary"]  # C4: name what's missing
    assert len(cards[0]["summary"]) < 140


def test_sglt2_via_medication_reference_is_silent():
    mr, med = sglt2_order_by_reference()
    r = client.post(URL, json=request_body(prefetch([T2D, CKD3], [egfr(41, 30)], [mr, med])))
    assert r.json() == {"cards": []}


def test_to_patient_data_sorts_resources():
    mr, med = sglt2_order_by_reference()
    outcome = {"resourceType": "OperationOutcome", "issue": []}
    p = cds_service.to_patient_data(PATIENT, [T2D, CKD3, egfr(41, 30), mr, med, outcome])
    assert p.id == PID and len(p.conditions) == 2 and len(p.egfr) == 1
    assert p.active_meds and p.active_meds[0][1] == "canagliflozin 100 MG Oral Tablet"


# ---------------------------------------------------------------- data in


def test_missing_prefetch_falls_back_to_fhir_server(monkeypatch):
    calls = []

    def fake_fetch(base, patient_id, token):
        calls.append((base, patient_id, token))
        return PATIENT, [T2D, CKD3, egfr(41, 30)]

    monkeypatch.setattr(cds_service, "from_fhir_server", fake_fetch)
    r = client.post(URL, json=request_body(fhir_server="http://ehr.example/fhir"))
    assert calls == [("http://ehr.example/fhir", PID, None)]
    assert len(r.json()["cards"]) == 1


def test_paged_prefetch_counts_as_incomplete():
    pf = prefetch([T2D, CKD3], [egfr(41, 30)])
    pf["egfr"] = bundle(egfr(41, 30), next_url="http://ehr.example/fhir?page=2")
    assert cds_service.from_prefetch(pf) is None


def test_no_prefetch_no_server_is_silent_not_error():
    r = client.post(URL, json=request_body())
    assert r.status_code == 200 and r.json() == {"cards": []}


def test_unreachable_fhir_server_is_silent_not_error():
    r = client.post(URL, json=request_body(fhir_server="http://127.0.0.1:9/fhir"))
    assert r.status_code == 200 and r.json() == {"cards": []}


# ---------------------------------------------------------------- bad requests


def test_unknown_service_404():
    assert client.post("/cds-services/nope", json=request_body()).status_code == 404


@pytest.mark.parametrize("hook", ["order-sign", None])
def test_wrong_hook_400(hook):
    body = request_body(prefetch([T2D]))
    body["hook"] = hook
    assert client.post(URL, json=body).status_code == 400


# ---------------------------------------------------------------- evidence (Phase 5)

EVIDENCE_URL = f"/evidence/{cds_service.SERVICE_ID}"


def test_evidence_matches_card_for_recommend():
    pf = prefetch([T2D, CKD3], [egfr(41, 30)])
    card = client.post(URL, json=request_body(pf)).json()["cards"][0]
    r = client.post(EVIDENCE_URL, json={"prefetch": pf})
    assert r.status_code == 200
    ev = r.json()
    assert ev["status"] == "RECOMMEND"
    # Same reasons the card's detail lists, line for line
    assert ev["reasons"] and all(f"- {reason}" in card["detail"] for reason in ev["reasons"])
    assert ev["facts"]["latest_egfr"][0] == 41
    assert ev["as_of"] == date.today().isoformat()
    assert ev["reviewed"] is cds_service.CLINICIAN_REVIEWED


@pytest.mark.parametrize("conditions, egfrs, expected, card_word", [
    ([T2D, CKD3], [egfr(41, 30)], "RECOMMEND", "Consider"),
    ([T2D, CKD3], [egfr(41, 800)], "INSUFFICIENT_DATA", "unclear"),
    ([T2D], [egfr(95, 30)], "NOT_APPLICABLE", None),
])
def test_evidence_status_agrees_with_hook(conditions, egfrs, expected, card_word):
    pf = prefetch(conditions, egfrs)
    cards = client.post(URL, json=request_body(pf)).json()["cards"]
    ev = client.post(EVIDENCE_URL, json={"prefetch": pf}).json()
    assert ev["status"] == expected
    if card_word is None:
        assert cards == []
    else:
        assert len(cards) == 1 and card_word in cards[0]["summary"]


def test_evidence_paged_prefetch_is_422_not_partial():
    pf = prefetch([T2D, CKD3], [egfr(41, 30)])
    pf["egfr"] = bundle(egfr(41, 30), next_url="http://ehr.example/fhir?page=2")
    r = client.post(EVIDENCE_URL, json={"prefetch": pf})
    assert r.status_code == 422 and "Incomplete" in r.json()["detail"]


def test_evidence_missing_prefetch_is_422():
    assert client.post(EVIDENCE_URL, json={}).status_code == 422


def test_evidence_unknown_service_404():
    assert client.post("/evidence/nope", json={"prefetch": {}}).status_code == 404


# ---------------------------------------------------------------- evidence link (Phase 5, S1-S2)


def evidence_links(card):
    return [l for l in card["links"] if l["label"] == cds_service.EVIDENCE_LABEL]


@pytest.mark.parametrize("egfrs", [[egfr(41, 30)], [egfr(41, 800)]], ids=["recommend", "insufficient"])
def test_card_has_one_launcher_link_for_this_patient(egfrs):
    card = client.post(URL, json=request_body(prefetch([T2D, CKD3], egfrs))).json()["cards"][0]
    links = evidence_links(card)
    assert len(links) == 1 and links[0]["type"] == "absolute"
    url = urlparse(links[0]["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == cds_service.LAUNCHER_URL
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert q == {
        "launch_uri": cds_service.APP_LAUNCH_URL,
        "fhir_server": cds_service.FHIR_FOR_LAUNCHER,
        "fhir_ver": "4",
        "patient": PID,
    }


def test_card_keeps_guideline_link():
    card = client.post(URL, json=request_body(prefetch([T2D, CKD3], [egfr(41, 30)]))).json()["cards"][0]
    assert any(l["url"] == cds_service.GUIDELINE_URL for l in card["links"])


def test_smart_mode_link_has_no_patient_in_url(monkeypatch):
    monkeypatch.setattr(cds_service, "EVIDENCE_LINK_MODE", "smart")
    card = client.post(URL, json=request_body(prefetch([T2D, CKD3], [egfr(41, 30)]))).json()["cards"][0]
    (link,) = evidence_links(card)
    assert link == {"label": cds_service.EVIDENCE_LABEL, "url": cds_service.APP_LAUNCH_URL, "type": "smart"}
    assert PID not in link["url"]
