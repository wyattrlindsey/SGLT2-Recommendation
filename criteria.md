# Criteria: SGLT2 inhibitor for adults with type 2 diabetes and CKD

Status: DRAFT (not yet reviewed by a clinician)
Owner: Wyatt Lindsey
Last updated: 2026-10-13

## Source

- **Guideline:** ADA Standards of Care in Diabetes—2026, Section 11 (Chronic Kidney Disease and Risk Management), subsection "Direct Kidney Effects of Glucose-Lowering Medications". Supporting source: KDIGO 2022 Clinical Practice Guideline for Diabetes Management in CKD.
- **Recommendation:** 11.7a, evidence grade **A**
- **Exact text:**

  > 11.7a For people with type 2 diabetes and CKD, use of a sodium–glucose cotransporter 2 (SGLT2) inhibitor with demonstrated benefit to reduce CKD progression and cardiovascular events is recommended. SGLT2 inhibitors should be initiated in individuals with eGFR ≥20 mL/min/1.73 m2 but can safely continue until kidney failure.

- **Supporting text from the same subsection:** SGLT2 inhibitors slow GFR loss "through mechanisms that appear independent of glycemia," and "kidney beneficial effects should be considered when selecting agents for glucose lowering."

### What the text settles, and what it leaves open

| Phrase in 11.7a | What it means for the code |
| --- | --- |
| "people with type 2 diabetes and CKD" | Population: T2D **and** CKD. No age, A1c, or CKD stage is named, so D1, D3 and D5 are still our decisions |
| "an SGLT2 inhibitor with demonstrated benefit" | Not every drug in the class qualifies. See D12 |
| "should be initiated in individuals with eGFR ≥20" | Starting threshold is ≥ 20, inclusive. Confirms D7 |
| "can safely continue until kidney failure" | Stopping is a different rule from starting. Patients already on the drug are out of scope for this rule (D8). Kidney failure is the end point, which supports the ESKD exclusion (D9) |
| Grade A | Strongest evidence level, from randomized trials |
| Glycemia-independent mechanism | The rule must not depend on A1c (no A1c check in `evaluate()`) |

### Out of scope for this rule

**11.7b** (a GLP-1 receptor agonist with demonstrated benefit for people with T2D and CKD, grade A) is a separate recommendation. It's a good candidate for the second rule, since it reuses the same population logic (D1–D6) with a different drug class. Don't fold it into this one. Keeping one recommendation per rule keeps every suggestion traceable to one sentence.

## Decisions

Each row is one ambiguity in the sentence above and how the code resolves it. Change a decision here first, then update the tests, then the code.

| # | Question | Decision | Why | Code |
| --- | --- | --- | --- | --- |
| D1 | Who counts as an adult? | Age 18 or older on the evaluation date | 11.7a names no age. Pediatric use is a separate question with its own evidence | `MIN_AGE` |
| D2 | What counts as type 2 diabetes? | Active, non-refuted Condition in `TYPE2_DIABETES`, including T2D complication codes | Synthea codes T2D complications separately | `valuesets.TYPE2_DIABETES` |
| D3 | Does a high A1c without a diagnosis count? | No (for now) | Keeps the first version simple. Revisit with a clinician. A1c plays no other role, because the benefit is independent of glycemia | — |
| D4 | What if type 1 diabetes is also coded? | Not applicable | 11.7a is for T2D. Conflicting codes need human review | `TYPE1_DIABETES` |
| D5 | What counts as CKD? | Any one of: (a) an active CKD diagnosis; (b) eGFR below 60 on 2 results at least 90 days apart in the last 2 years; (c) UACR of 30 mg/g or more on 2 results at least 90 days apart | 11.7a doesn't define CKD. KDIGO defines it as abnormal for more than 3 months. Diagnoses are often missing from real problem lists | `CKD`, `persistent()` |
| D6 | How recent must the eGFR be? | Within 365 days | Kidney function changes. A stale value can't support a decision about starting a drug | `EGFR_LOOKBACK_DAYS` |
| D7 | What eGFR is needed to start? | Latest eGFR **≥ 20** (20.0 qualifies, 19.9 doesn't) | 11.7a: "should be initiated in individuals with eGFR ≥20" | `EGFR_INITIATION_MIN` |
| D8 | What about patients already on an SGLT2 inhibitor? | Not applicable to this rule, whatever their eGFR | 11.7a separates starting (≥ 20) from continuing ("until kidney failure"). This rule only answers "should this patient start?" A continuation rule would be a separate rule | `SGLT2_INHIBITORS` |
| D9 | Exclusions? | End-stage kidney disease or dialysis | 11.7a says the drugs continue "until kidney failure," so kidney failure is outside the recommendation | `ESKD` |
| D10 | Missing data? | Return INSUFFICIENT_DATA, not "no" | "We don't know" is different from "not eligible," and a clinician should see that | `evaluate()` |
| D11 | Other contraindications (pregnancy, recurrent ketoacidosis, allergy)? | _Open — ask a clinician_ | 11.7a doesn't list them | |
| D12 | Which SGLT2 inhibitors have "demonstrated benefit to reduce CKD progression and cardiovascular events"? | Canagliflozin, dapagliflozin and empagliflozin. A RECOMMEND result names these three | Each has a dedicated kidney outcome trial (CREDENCE, DAPA-CKD and EMPA-KIDNEY). Ertugliflozin and bexagliflozin have no kidney outcome trial. **Confirm with a clinician** | `SGLT2_DEMONSTRATED_BENEFIT` |
| D13 | Does "already on an SGLT2 inhibitor" (D8) include one *without* demonstrated benefit (ertugliflozin, bexagliflozin)? | Yes, still not applicable, but the reason says the agent lacks kidney outcome evidence so a clinician can decide whether to switch | Suggesting a second drug from the same class would be wrong. Silently treating ertugliflozin as equal would hide what 11.7a says | `evaluate()` |
| D14 | What about patients who have died? | Not applicable if `Patient.deceasedDateTime` is on or before the evaluation date, or `deceasedBoolean` is true. A death dated after the evaluation date is ignored, so `--as-of` still works | Found in hand review (patient 112236, Synthea keeps patients who died). The code had said INSUFFICIENT_DATA because their last eGFR was from 2017 | `evaluate()` step 0 |
| D15 | What if there are several eGFR results on the latest day? | If they fall on both sides of 20, return INSUFFICIENT_DATA ("conflicting eGFR results"). Otherwise use the lowest | Found in hand review (patient 220864: 54.377 and 16.41 at the same time, same encounter; the code picked one arbitrarily and said RECOMMEND). Real records also have repeat draws and corrected results. Flagging the conflict is more honest than picking one | `latest_day()`, `evaluate()` step 5 |

## Card behavior (Phase 4)

How the CDS Hooks service (`cds_service.py`) turns a `Result` into what the clinician sees. D1–D15 decide *what the rule concludes*; C1–C7 decide *when and how to say it*. Same process: change a decision here first, then the tests, then the code.

| # | Question | Decision | Why | Code |
| --- | --- | --- | --- | --- |
| C1 | What if prefetch is missing, null, or a search Bundle has a `next` page? | Treat it as incomplete and run the same queries against `fhirServer`, following every page. If there's no `fhirServer`, return no cards | A partial eGFR history could flip D5 (persistence) or D7 (latest value). Never decide on part of the record | `from_prefetch()`, `from_fhir_server()` |
| C2 | Which statuses get a card? | RECOMMEND and INSUFFICIENT_DATA get one card each. NOT_APPLICABLE gets none | `patient-view` fires on every chart opened. A card on every non-diabetic chart teaches clinicians to ignore the service (alert fatigue). INSUFFICIENT_DATA is shown because "order an eGFR" is useful advice (D10) | `cards_for()` |
| C3 | Which indicator? | `info` for both cards | `warning` and `critical` are for harm if ignored. Not yet starting a guideline-recommended drug isn't urgent at chart open. Revisit for `order-select`, where the clinician is choosing a diabetes drug right now | `cards_for()` |
| C4 | What does the card say? | Summary (under 140 characters) names the drug class, the reason and the source. RECOMMEND: "Consider an SGLT2 inhibitor: type 2 diabetes with CKD, eGFR ≥ 20 (ADA 11.7a)". INSUFFICIENT_DATA: "SGLT2 inhibitor eligibility unclear: " + what's missing (the last reason). Detail lists every reason from `evaluate()`. Source points to ADA Section 11. Links: "Review the evidence" (S1) first, then ADA Section 11 | The summary is often all a clinician reads, so it must stand alone. Listing every reason lets them check the logic against the chart | `cards_for()`, `_card()` |
| C5 | Verify the EHR's JWT? | Not in the prototype. Log whether one was sent | Needed before any real deployment (CDS Hooks 2.0 requires it). Not needed for synthetic data and the public sandbox | `call_service()` |
| C6 | Offer an order (a `suggestion`)? | No. Every card also says the logic hasn't been reviewed by a clinician | This spec hasn't had clinician sign-off (Phase 3, Step 7 deferred). Both behaviors are tied to one flag; flip it only after sign-off | `CLINICIAN_REVIEWED` |
| C7 | What if anything throws? | Log the error and return `{"cards": []}` with HTTP 200 | A decision support service must never break the chart screen. The test guard (`no_hidden_errors`) makes sure this doesn't hide bugs in testing | `call_service()` |

## Evidence app (Phase 5)

How the SMART on FHIR evidence app (`smart_app/`) and the `/evidence` endpoint behave. C1–C7 decide what the card says; S1–S8 decide what the clinician sees when they open the card's link to check *why*. Same process: change a decision here first, then the tests, then the code.

| # | Question | Decision | Why | Code |
| --- | --- | --- | --- | --- |
| S1 | Which cards get the link? | RECOMMEND and INSUFFICIENT_DATA, the two statuses that get a card (C2). One "Review the evidence" link per card, before the ADA link | For INSUFFICIENT_DATA the app is the most useful: it shows exactly what's missing, which a 140-character summary can't | `evidence_link()`, `_card()` |
| S2 | Which link type? | `absolute`, pointing at the SMART launcher's `/remote-launch` with the app's launch URL, the FHIR server and the patient ID. `smart` once an EHR can do the launch itself | The CDS Hooks sandbox can only launch `smart` links against a FHIR server with its non-standard `_services/smart/Launch` endpoint, which HAPI lacks. The launcher plays the EHR's launch step instead. In `smart` mode the URL carries no patient: the EHR supplies context | `EVIDENCE_LINK_MODE` |
| S3 | Where does the rule run? | Only in the service. The app posts data to `/evidence`, which calls `evaluate_prefetch()`, the same function the hook uses. No copy of the rule in JavaScript | Two copies of the rule would drift apart, and the card and the app would then disagree about the same patient | `evaluate_prefetch()`, `evidence()` |
| S4 | Where does the app's data come from? | The app reads it from the FHIR server with its own access token, using the prefetch templates from the discovery endpoint, and sends the Bundles to `/evidence`. The token never leaves the app | Sending the token to the service would mean a second server holding a token it wasn't issued, which a real EHR may reject. Reusing the templates means the app and the hook ask for exactly the same data | `smart_app/app.js` (`runPrefetch()`) |
| S5 | Which scopes? | `launch patient/*.read`: read-only, limited to the launched patient. No `openid` or `fhirUser` for now | Ask for the least the app needs. With `openid` and `fhirUser`, the launcher adds a provider login, which the prototype doesn't use yet | `smart_app/config.js` |
| S6 | Writes or orders? | None. The app shows the "not yet reviewed by a clinician" line while `CLINICIAN_REVIEWED` is False | Same reason as C6: this spec hasn't had clinician sign-off. One flag controls the card and the app together | `CLINICIAN_REVIEWED`, `evidence()` (`reviewed`) |
| S7 | What if the data is incomplete? | If a Bundle is missing or has a `next` page, `/evidence` returns HTTP 422 and evaluates nothing, and the app says the data is incomplete. No `fhirServer` fallback | Same principle as C1: never decide on part of the record. Unlike the hook, the service has no token of its own to fetch more with, and shouldn't fetch on the app's behalf | `from_prefetch()`, `evidence()` |
| S8 | What gets stored or logged? | The service logs the status only, not the reasons. The app stores nothing itself; `fhirclient` keeps the token in `sessionStorage` for that browser tab | Reasons contain clinical details. Same habit as the hook's logging, formed before real data exists | `evidence()`, `smart_app/app.js` |

## Truth table (hand-checked)

Fill this in from the cohort run (Step 6 in the guide). Pick some patients the code marks RECOMMEND and some it doesn't, read their full record, and record what you conclude yourself before you look at the code's answer.

| Patient ID | Name | My answer | Code's answer | Agree? | Notes |
| --- | --- | --- | --- | --- | --- |
| 101314 | Eunice Beahan | Not applicable | Not applicable | Yes | No evidence of CKD |
| 123560 | Jody Hickle | Recommend | Recommend | Yes | Has Type 2 Diabetes, has active Stage 1 CKD, latest eGFR ≥20 and with 365 days, is not currently on an SGLT2, and does not have ESKD |
| 112236 | Parker Hintz | Not applicable | Insufficient data | No | Patient is deceased, meaning the decision should be "not applicable" instead of "insufficient data". Fixed by D14; |
| 138555 | Larissa Golner | Not applicable | Not applicable | Yes | No evidence of CKD |
| 216334 | Dong Borer | Not applicable | Not applicable | Yes | Has active type 2 diabetes and CKD, but latest eGFR is below threshold |
| 220864 | Brianna Pagac | Insufficient data | Recommend | No | Has active type 2 diabetes and CKD, but has two eGFR values on the most recent day (one over 20, and one under 20). Fixed by D15 |
| 237926 | Guillermina Gamez | Recommend | Recommend | Yes | Has active type 2 diabetes and CKD, latest eGFR is above threshold, no ESKD, and not currently taking an SGLT2 inhibitor |
| 244324 | Roxanne Herzog | Insufficient data | Insufficient data | Yes | Has active type 2 diabetes and CKD, but there is no recent eGFR data |
| 258353 | Gerda Trantow | Not applicable | Not applicable | Yes | Has active ESKD |
| 310873 | Kenneth Ritchie | Recommend | Recommend | Yes | Has active type 2 diabetes and CKD, latest eGFR is above threshold, no ESKD, and not currently taking an SGLT2 inhibitor |

## Open questions for clinical review

- [ ] Is D5 (the CKD definition) acceptable? Should a single very low eGFR count?
- [ ] Should D3 change (count patients with a high A1c but no T2D diagnosis)?
- [ ] Which contraindications belong in D11?
- [ ] D12: Is the list of agents with demonstrated benefit right, and should it change as new trials report?
- [ ] D13: For a patient on ertugliflozin or bexagliflozin, should the tool suggest switching?
- [ ] D15: Is "same day" the right grouping for conflicting eGFR results, and should the lowest value win when they agree?
- [ ] Should there be a separate continuation rule (on an SGLT2 inhibitor, eGFR below 20, not on dialysis)? What would it tell the clinician?
- [ ] Should 11.7b (GLP-1 receptor agonist) be the next rule?
- [ ] C2: Is an INSUFFICIENT_DATA card worth showing for every cause (no birth date, no recent eGFR, conflicting eGFRs), or only when a lab order would resolve it?
- [ ] C3/C4: Is `info` the right level, and is the card wording clear and appropriately cautious?
- [ ] C6: Once the spec is signed off, should the card offer a draft order, and for which agent and dose?
- [ ] S1: Does the evidence app show what a clinician needs to trust or reject the recommendation? What's missing, and what's noise?
