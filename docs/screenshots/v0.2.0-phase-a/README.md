# v0.2.0 Phase A — Safari acceptance evidence

This is a bounded local-browser evidence record for the read-only NESO carbon-intensity panel. It is not a release record or a production deployment claim.

## Capture

- **Browser:** Apple Safari on macOS.
- **Target:** local dashboard on `127.0.0.1`; not a deployed service.
- **Page-reported retrieval time:** 29 October 2026 at 12:18 UTC.
- **Image:** [Safari live carbon-intensity panel](safari-live-carbon-intensity-stale-1568x1550.png), 1568 × 1550 pixels.
- **SHA-256:** `f6e73cc728eb2de70e20f4d6a4989df61a6b41992805108a9a575c06b30f0d3d`.
- **Privacy check:** the image was visually inspected before inclusion. It shows a loopback address and application state; no credentials, tokens, personal data, or private endpoint are visible.

## What the screenshot demonstrates

- The panel renders region 13 (London), `218 gCO2/kWh`, forecast provenance, the 11:30–12:00 UTC interval, and its displayed retrieval time.
- The UI marks the forecast stale (48 minutes at capture) and warns that it covers a completed interval rather than a current reading.
- The source-boundary card labels the schedule inputs as synthetic and the live display as **NOT CONNECTED**. The page also identifies the local simulation-only, no-actuation context.
- A separate local runtime check exercised `GET /api/v1/energy/carbon-intensity` and observed HTTP 200; that response is runtime evidence, not something established by the screenshot pixels.

## What it does not demonstrate

- The stale forecast is **not** evidence of a current carbon-intensity reading. The screenshot alone does not authenticate the upstream payload or prove upstream availability beyond the captured display.
- It does not establish production deployment, DNS/TLS configuration, CI results, uptime, or provider-side settings.
- It does not verify other browsers, mobile layouts, keyboard or focus behaviour, a full accessibility audit, or behaviour after the capture time.
- It does not show live NESO data feeding the scheduler; the displayed source boundary says it is disconnected. It makes no measured carbon-savings or trading claim.
