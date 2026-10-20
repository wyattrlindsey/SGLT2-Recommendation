// Settings for the SGLT2 evidence app. Change these, not app.js.
window.APP_CONFIG = {
  // Public client: no secret. The SMART launcher accepts any client ID;
  // a real EHR (or OpenEMR) makes you register it first.
  clientId: "sglt2-evidence",

  // Read-only, patient-level (S5). "launch" asks for the EHR's patient context.
  // No openid/fhirUser on purpose: with those, the launcher shows a provider login.
  scope: "launch patient/*.read",

  // The Phase 4 CDS service. The app reads its prefetch templates from discovery
  // and asks it to evaluate the data (S3, S4).
  serviceBase: "http://localhost:8000",
  serviceId: "sglt2-t2d-ckd",

  // D7: eGFR threshold for starting an SGLT2 inhibitor. Only used to draw a line
  // on the chart. The decision itself comes from the service.
  egfrThreshold: 20
};