# CDS Hook for SGLT2 Recommendation

[![tests](https://github.com/wyattrlindsey/SGLT2-Recommendation/actions/workflows/tests.yml/badge.svg)](https://github.com/wyattrlindsey/SGLT2-Recommendation/actions/workflows/tests.yml)

A clinical decision support service that reads a patient's record over FHIR and, when the ADA Standards of Care call for it, suggests an SGLT2 inhibitor for adults with type 2 diabetes and chronic kidney disease. It speaks [CDS Hooks](https://cds-hooks.hl7.org/), the HL7 standard EHRs use to call outside advice services, and returns a suggestion card the way a clinician would see it inside an EHR. A small SMART on FHIR app, opened from the card, shows the evidence behind each suggestion.

> **Not for clinical use.** This project runs on synthetic patients only. Its output is not medical advice, and the rule has not yet been reviewed by a clinician: every card says so and none offers to place an order.

## What it does

When a clinician opens a patient's chart, the EHR sends a `patient-view` hook to the service with the patient's conditions, kidney labs and active medications. The service evaluates ADA recommendation 11.7a and sorts the patient into one of three results:

| Result | What the clinician sees |
| --- | --- |
| **RECOMMEND** | A card suggesting an SGLT2 inhibitor, with every reason the rule fired |
| **INSUFFICIENT_DATA** | A card saying eligibility is unclear and what's missing (for example, no eGFR in the last year) |
| **NOT_APPLICABLE** | Nothing. Staying silent on irrelevant charts avoids alert fatigue |

The rule is tested against hand-built patients, hand-checked against 10 synthetic patients in a truth table, and the service agrees with the standalone rule on all 64 diabetic patients in the synthetic cohort.

## Example

The request, as an EHR would send it (from [`hook.http`](hook.http)). With no `prefetch`, the service fetches the data itself from `fhirServer`:

```http
POST http://localhost:8000/cds-services/sglt2-t2d-ckd
Content-Type: application/json

{
  "hook": "patient-view",
  "hookInstance": "manual-recommend-310873",
  "fhirServer": "http://localhost:8080/fhir",
  "context": { "userId": "Practitioner/example", "patientId": "310873" }
}
```

The card that comes back for a RECOMMEND patient (generated from the test suite's RECOMMEND fixture; `uuid` and link URLs shortened):

```json
{
  "cards": [
    {
      "uuid": "b3bc0fd8-…",
      "summary": "Consider an SGLT2 inhibitor: type 2 diabetes with CKD, eGFR ≥ 20 (ADA 11.7a)",
      "detail": "This patient meets ADA 11.7a for starting an SGLT2 inhibitor:\n\n- Type 2 diabetes (Diabetes mellitus type 2)\n- CKD diagnosis: Chronic kidney disease stage 3\n- Latest eGFR 41 on 2026-09-29 (>= 20)\n- Not currently on an SGLT2 inhibitor\n- Agents with demonstrated benefit (ADA 11.7a): canagliflozin, dapagliflozin, empagliflozin\n\n_Prototype on synthetic data. Rule logic has not yet been reviewed by a clinician._",
      "indicator": "info",
      "source": {
        "label": "ADA Standards of Care 2026, rec. 11.7a",
        "url": "https://diabetesjournals.org/care/article/49/Supplement_1/S246/…"
      },
      "links": [
        { "label": "Review the evidence", "url": "http://localhost:9000/remote-launch?…", "type": "absolute" },
        { "label": "ADA Standards of Care, Section 11", "url": "https://diabetesjournals.org/…", "type": "absolute" }
      ]
    }
  ]
}
```

## How the logic works

The guideline is one sentence: *"For people with type 2 diabetes and CKD, use of an SGLT2 inhibitor with demonstrated benefit … is recommended. SGLT2 inhibitors should be initiated in individuals with eGFR ≥20."* Turning it into code means answering everything the sentence leaves open: which diagnosis codes count, how CKD is defined when it isn't on the problem list, how recent a lab must be, what to do when data is missing or contradictory.

Every one of those choices is written down in **[`criteria.md`](criteria.md)**, with the reason for it and the constant in the code that implements it:

- **D1–D15**: what the rule concludes (for example, D5 accepts CKD by diagnosis *or* by two abnormal lab results at least 90 days apart; D10 returns "insufficient data" rather than "no" when labs are missing)
- **C1–C7**: when and how the service speaks (for example, C2 keeps NOT_APPLICABLE silent; C7 makes sure an error never breaks the chart screen)
- **S1–S8**: how the evidence app gets its data and what it shows
- **Truth table**: 10 synthetic patients reviewed by hand before looking at the code's answer. Two disagreements found there became decisions D14 (deceased patients) and D15 (conflicting same-day eGFR results)

The code is split into three layers so the rule itself never touches the network: [`guideline.py`](guideline.py) fetches FHIR data, turns it into a small `PatientData` object, and runs `evaluate()` on it. [`cds_service.py`](cds_service.py) feeds the hook's prefetched data into the same `evaluate()`, and the evidence app's `/evidence` endpoint uses the same code path, so the card and the app can't disagree. Code lists (SNOMED CT, LOINC, RxNorm) live in [`valuesets.py`](valuesets.py).

## Roadmap

| Phase | Goal | Status |
| --- | --- | --- |
| 1. Local FHIR server | Run a HAPI FHIR server loaded with Synthea patients, and query it with curl and Python | Done |
| 2. Generate patients | Create synthetic patients and explore full records | Done |
| 3. Encode one guideline | Turn ADA 11.7a into code with defined code sets, and check it by hand | Done |
| 4. CDS Hooks service | Wrap the logic as a web service and test it in the CDS Hooks sandbox | Done |
| 5. SMART on FHIR app | Open an evidence app from the card: eGFR trend, each criterion met or missing, current medications | In progress (`/evidence` endpoint and card link done) |
| Next | Clinician review of `criteria.md`; a second rule (ADA 11.7b, GLP-1 receptor agonists) | Not started |
| Later | CQL for portable rules; connect to REDCap study data | Not started |

## Repository layout

```
.
├── guideline.py              # The rule: fetch FHIR data, build PatientData, evaluate(). Also a CLI
├── valuesets.py              # Code lists for each clinical concept (SNOMED CT, LOINC, RxNorm)
├── build_sglt2_valueset.py   # Expands SGLT2 ingredients into every RxNorm product via RxNav
├── sglt2_rxnorm.json         # Output of the above (a code list, not patient data)
├── cds_service.py            # FastAPI CDS Hooks service: discovery, patient-view hook, /evidence
├── criteria.md               # The spec: every decision, card behavior, truth table
├── test_guideline.py         # Tests for the rule, using hand-built patients
├── test_cds_service.py       # Tests for the service: cards, prefetch, fallback, errors
├── parity_check.py           # Checks the running service agrees with guideline.py
├── hook.http                 # Sample hook requests (VS Code REST Client)
├── smart_app/                # SMART on FHIR evidence app (fhirclient)
├── load_synthea.py           # Loads Synthea bundles into a FHIR server, in the right order
├── explore.py                # First queries: counts, condition codes, a diabetes cohort
├── exercises/                # Phase 1 practice exercises
├── docker-compose.yml        # HAPI FHIR with PostgreSQL, so data survives restarts
└── hapi.application.yaml     # HAPI database settings used by docker-compose.yml
```

## Quick start

**Requirements:** Docker Desktop (4 GB+ memory), Java JDK 17 or newer, Python 3.10+.

The tests need no server:

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

To run the whole thing against synthetic patients:

1. Start the FHIR server (HAPI with PostgreSQL):

   ```bash
   docker compose up -d
   ```

2. Download `synthea-with-dependencies.jar` from the [Synthea setup page](https://github.com/synthetichealth/synthea/wiki/Basic-Setup-and-Running) into the repo folder, generate patients, and load them:

   ```bash
   java -jar synthea-with-dependencies.jar -s 42 -p 15 -a 40-80 Massachusetts
   python load_synthea.py --dir ./output/fhir --base http://localhost:8080/fhir
   ```

3. Run the rule on one patient, or on every diabetic patient:

   ```bash
   python guideline.py --base http://localhost:8080/fhir --patient PATIENT_ID
   python guideline.py --base http://localhost:8080/fhir --csv cohort.csv
   ```

   Synthea dates run up to the day the data was generated. If everyone comes back INSUFFICIENT_DATA, add `--as-of YYYY-MM-DD` with that date.

4. Start the CDS Hooks service, then send it the requests in `hook.http`:

   ```bash
   python -m uvicorn cds_service:app --reload --port 8000
   ```

5. See the card in the public [CDS Hooks sandbox](https://sandbox.cds-hooks.org), which plays the EHR. This link points it at your local FHIR server, a patient and your service (change `patientId`):

   ```text
   https://sandbox.cds-hooks.org/?fhirServiceUrl=http%3A%2F%2Flocalhost%3A8080%2Ffhir&patientId=310873&serviceDiscoveryURL=http%3A%2F%2Flocalhost%3A8000%2Fcds-services
   ```

6. Check the service and the rule agree (with both running):

   ```bash
   python parity_check.py --all --out parity.csv
   ```

To stop and restart the FHIR server without losing data, use `docker compose stop` and `docker compose start`.

## Reproducing the data

Generated data is not committed. Running Synthea with the same seed recreates the same patients:

```bash
java -jar synthea-with-dependencies.jar -s 42 -p 15 -a 40-80 Massachusetts
```

If you change the seed, population size, or age range, update the command here so others can reproduce your data.

## Data rules

- **Synthetic data only** for now. Real patient data is governed by HIPAA and requires working inside a healthcare institution.
- **Real patient data never goes in this repository**, public or private. Cohort CSVs and loader logs are excluded by `.gitignore` even though they're synthetic, so the habit is in place before real data exists.
- The Synthea jar and the generated `output/` folder are excluded too. They're large (the jar is about 200 MB, and single patient files can be tens of MB) and can always be regenerated.
- No rule is used beyond synthetic data, and no card offers an order, until a clinician has signed off its spec.

## Key standards

| Term | What it is |
| --- | --- |
| FHIR | REST API standard for clinical data, with resources like `Patient` and `Observation` as JSON |
| CDS Hooks | Spec where an EHR sends patient context to a service and gets back suggestion "cards" |
| SMART on FHIR | OAuth 2.0 based way for an app to launch inside an EHR and read a patient's data |
| Synthea | Open-source generator of realistic synthetic patients as FHIR |
| HAPI FHIR | Open-source FHIR server, run locally in Docker |
| LOINC, SNOMED CT, ICD-10, RxNorm | Code systems for labs, clinical concepts, diagnoses, and drugs |
| REDCap | Web platform for collecting and storing research study data |

## Project lead

Wyatt Lindsey
