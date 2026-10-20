// SGLT2 evidence app (Phase 5).
//
// Step 2 of the EHR launch: FHIR.oauth2.ready() finishes the OAuth exchange and
// returns a client bound to the launched patient and an access token. Then:
//   1. Read the prefetch templates from the CDS service's discovery endpoint (S4)
//   2. Run each query against the EHR's FHIR server with the app's own token
//   3. Send those Bundles to the service's /evidence endpoint, which runs the
//      same evaluate() the card used (S3)
//   4. Show the result next to the data it came from
// The rule never runs in this file.

const C = window.APP_CONFIG;
const app = document.getElementById("app");

FHIR.oauth2.ready().then(main).catch(showError);

async function main(client) {
  const patientId = client.patient.id;
  if (!patientId) throw new Error("No patient in launch context. Was 'launch' in the scope?");

  const templates = await getPrefetchTemplates();
  const prefetch = await runPrefetch(client, templates, patientId);
  const incomplete = Object.entries(prefetch)
    .filter(([, r]) => r && r.resourceType === "Bundle" && (r.link || []).some(l => l.relation === "next"))
    .map(([k]) => k);

  const evidence = await postEvidence(prefetch);
  render({ patient: prefetch.patient, prefetch, evidence, incomplete, iss: client.state.serverUrl });
}

async function getPrefetchTemplates() {
  const r = await fetch(`${C.serviceBase}/cds-services`);
  if (!r.ok) throw new Error(`Discovery failed: HTTP ${r.status}`);
  const svc = (await r.json()).services.find(s => s.id === C.serviceId);
  if (!svc) throw new Error(`Service '${C.serviceId}' not in discovery`);
  return svc.prefetch;
}

// Fill {{context.patientId}} and re-encode the query string, the way an EHR does
// when it runs prefetch. Values like "http://loinc.org|33914-3,..." need encoding.
function fillTemplate(template, patientId) {
  const filled = template.replaceAll("{{context.patientId}}", patientId);
  const [path, query] = filled.split("?");
  if (!query) return path;
  const params = new URLSearchParams();
  for (const pair of query.split("&")) {
    const i = pair.indexOf("=");
    params.append(pair.slice(0, i), pair.slice(i + 1));
  }
  return `${path}?${params.toString()}`;
}

async function runPrefetch(client, templates, patientId) {
  const keys = Object.keys(templates);
  // client.request() resolves the URL against iss and adds the Bearer token.
  const results = await Promise.all(keys.map(k => client.request(fillTemplate(templates[k], patientId))));
  return Object.fromEntries(keys.map((k, i) => [k, results[i]]));
}

async function postEvidence(prefetch) {
  const r = await fetch(`${C.serviceBase}/evidence/${C.serviceId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prefetch })
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) return { error: body.detail || `HTTP ${r.status}` };
  return body;
}

// ---------- rendering ----------

const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const entries = b => (b && b.entry ? b.entry.map(e => e.resource) : []);

function patientHeader(p) {
  const n = (p.name || [])[0] || {};
  const name = [...(n.given || []), n.family].filter(Boolean).join(" ") || "(no name)";
  return `<h1>${esc(name)}</h1>
    <div class="muted">Born ${esc(p.birthDate || "unknown")} · ${esc(p.gender || "")} · Patient/${esc(p.id)}</div>`;
}

function observations(bundle) {
  return entries(bundle)
    .filter(o => o.resourceType === "Observation" && o.valueQuantity)
    .map(o => ({
      date: (o.effectiveDateTime || o.issued || "").slice(0, 10),
      value: o.valueQuantity.value,
      unit: o.valueQuantity.unit || o.valueQuantity.code || "",
      code: (o.code.coding || [])[0]?.code || "",
      status: o.status
    }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

function chart(points, threshold) {
  if (points.length === 0) return `<p class="muted">No results.</p>`;
  const W = 760, H = 180, P = 32;
  const t = points.map(p => Date.parse(p.date));
  const t0 = Math.min(...t), t1 = Math.max(...t) || t0 + 1;
  const vmax = Math.max(threshold * 1.5, ...points.map(p => p.value)) * 1.1;
  const x = d => P + ((Date.parse(d) - t0) / Math.max(t1 - t0, 1)) * (W - 2 * P);
  const y = v => H - P - (v / vmax) * (H - 2 * P);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(p.date).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="eGFR over time">
    <line x1="${P}" x2="${W - P}" y1="${y(threshold)}" y2="${y(threshold)}" stroke="#c0392b" stroke-dasharray="4 4"/>
    <text x="${W - P}" y="${y(threshold) - 4}" text-anchor="end">${threshold} (D7)</text>
    <path d="${line}" fill="none" stroke="#2c5aa0" stroke-width="2"/>
    ${points.map(p => `<circle cx="${x(p.date)}" cy="${y(p.value)}" r="3" fill="#2c5aa0"><title>${esc(p.date)}: ${esc(p.value)} ${esc(p.unit)}</title></circle>`).join("")}
    <text x="${P}" y="${H - 8}">${esc(points[0].date)}</text>
    <text x="${W - P}" y="${H - 8}" text-anchor="end">${esc(points.at(-1).date)}</text>
  </svg>`;
}

function obsTable(points) {
  if (points.length === 0) return "";
  return `<table><tr><th>Date</th><th>Value</th><th>LOINC</th><th>Status</th></tr>
    ${points.slice().reverse().map(p => `<tr><td>${esc(p.date)}</td><td>${esc(p.value)} ${esc(p.unit)}</td><td>${esc(p.code)}</td><td>${esc(p.status)}</td></tr>`).join("")}
  </table>`;
}

function medList(bundle) {
  const all = entries(bundle);
  const meds = Object.fromEntries(all.filter(r => r.resourceType === "Medication").map(m => [`Medication/${m.id}`, m]));
  const rows = all.filter(r => r.resourceType === "MedicationRequest").map(mr => {
    const cc = mr.medicationCodeableConcept || meds[mr.medicationReference?.reference]?.code || {};
    const c = (cc.coding || [])[0] || {};
    return `<tr><td>${esc(cc.text || c.display || "(unnamed)")}</td><td>${esc(c.code || "")}</td><td>${esc((mr.authoredOn || "").slice(0, 10))}</td></tr>`;
  });
  if (rows.length === 0) return `<p class="muted">No active medication requests.</p>`;
  return `<table><tr><th>Medication</th><th>RxNorm</th><th>Ordered</th></tr>${rows.join("")}</table>`;
}

function render({ patient, prefetch, evidence, incomplete, iss }) {
  const egfr = observations(prefetch.egfr);
  const uacr = observations(prefetch.uacr);
  const ev = evidence.error
    ? `<div class="panel error"><b>The service could not evaluate this patient.</b><br>${esc(evidence.error)}</div>`
    : `<div class="panel ${esc(evidence.status)}">
         <div class="status">${esc(evidence.status)}</div>
         <div class="muted">Evaluated as of ${esc(evidence.as_of)} by ${esc(C.serviceId)}${evidence.reviewed ? "" : " · Logic not yet reviewed by a clinician"}</div>
         <ul>${(evidence.reasons || []).map(r => `<li>${esc(r)}</li>`).join("")}</ul>
       </div>`;

  app.innerHTML = `
    ${patientHeader(patient)}
    ${incomplete.length ? `<div class="panel warn">Incomplete data: ${esc(incomplete.join(", "))} came back with more pages. Nothing was decided on partial data (S7).</div>` : ""}
    ${ev}
    <div class="panel"><h2>eGFR</h2>${chart(egfr, C.egfrThreshold)}${obsTable(egfr)}</div>
    <div class="panel"><h2>UACR</h2>${obsTable(uacr) || `<p class="muted">No results.</p>`}</div>
    <div class="panel"><h2>Active medications</h2>${medList(prefetch.meds)}</div>
    ${evidence.facts ? `<div class="panel"><h2>Facts the rule used</h2><pre>${esc(JSON.stringify(evidence.facts, null, 2))}</pre></div>` : ""}
    <p class="muted">FHIR server: ${esc(iss)}</p>`;
}

function showError(err) {
  console.error(err);
  app.innerHTML = `<div class="panel error"><b>Launch failed.</b><br>${esc(err.message || err)}
    <p class="muted">Open this app from the card link, not directly. See Troubleshooting in the Phase 5 guide.</p></div>`;
}