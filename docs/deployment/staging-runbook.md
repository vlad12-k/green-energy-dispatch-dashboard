# Staging deployment runbook

**Status:** the owner-provided deployment log explicitly records that the existing public Render service checked out application commit `55ae4f274848ac3642e1e0bf804feba7f07b8659` from `main` and reached Live status. This establishes the logged deployment revision, not an independent readback of the currently active Dashboard state. The current Compute Plan and Auto-Deploy setting remain unconfirmed. The v0.2.0 Phase A branch candidate is not deployed; this runbook update does not authorise a new resource, setting change, Blueprint sync, or deployment. The v0.1.0 service and release remain unchanged.

## Decision and topology

The intended topology uses one Render Docker web service for the React application and FastAPI API:

```text
Browser: https://green-energy-dispatch-dashboard.onrender.com
  GET / and client-side routes  ──> FastAPI serves frontend/dist/index.html
  GET /assets/*                ──> FastAPI serves built Vite assets
  POST /api/v1/simulations/ev-depot/evaluate ──> stateless synthetic evaluator
  GET /api/v1/energy/carbon-intensity      ──> read-only configured NESO region (v0.2 candidate)
  POST /api/v1/energy/carbon-intensity     ──> 405; Allow: GET; no upstream request
  Other /api/* routes                     ──> 404 in SIMULATION_ONLY_MODE
```

The v0.2.0 candidate has the new route in code but has not been deployed. The published v0.1.0 service is unchanged. Its actual GET response is not exercised by the staging smoke test to avoid making an external NESO request; validate real data only in a bounded, explicitly authorised local/browser acceptance path.

The production Vite build uses an empty `VITE_API_BASE_URL`, so browser requests stay on the page's origin. This avoids CORS configuration, a second hostname, and browser credentials. The Render service provides an `onrender.com` hostname and managed TLS; no custom domain is required.

The EV depot evaluator remains stateless. The Blueprint does not provision PostgreSQL, a persistent disk, or a NESO credential. The v0.2.0 API candidate reads public NESO data through its fixed allowlisted HTTPS origin and writes neither the scenario nor the live response to persistence. Do not add a database unless a later, explicitly scoped feature introduces durable application state.

## Public-demo boundary

- Set `SIMULATION_ONLY_MODE=true`; allow only synthetic `POST /api/v1/simulations/ev-depot/evaluate` and (for the v0.2.0 candidate) read-only `GET /api/v1/energy/carbon-intensity` under `/api`.
- The NESO route uses only the fixed official HTTPS host, disables redirects, validates the response, caps response size at 64 KiB, and bounds the timeout to 30 seconds maximum (5 seconds by default). Its freshness-aware process cache lasts at most 30 seconds. Stale/error results are not publicly cached.
- Disable debug and OpenAPI docs (`DEBUG=false`).
- Bound the synthetic evaluator's request body to 65,536 bytes.
- Apply a separate 60-request rolling limit per application process for each public route. These in-memory limits are shared among visitors of a process, reset on restart, and are not per-IP or coordinated across replicas. Before broader promotion, add and verify an edge/per-client limiter and an abuse alert path.
- The evaluator accepts synthetic scenario input only and does not claim real-world emissions reductions. It does not persist submitted scenarios. The NESO GET is for display only and is not connected to the scheduler.
- No browser API token or authentication is required; anything embedded in frontend code is public, not a secret. Do not submit personal, operational, or confidential data.

## Provider cost and constraints

The checked-in Blueprint specifies Render's **Free** web-service plan (`plan: free`): at the time the provider documentation was reviewed for this candidate it described **0.1 CPU and 512 MB RAM**. This is the Blueprint's intended target, not verification of the existing service's plan. It is a portfolio/demo tier, not a production availability target. Confirm current plan, quotas, workspace usage, billing state, and provider terms directly in the account before relying on any figure; this repository does not reveal whether a payment method or overage billing is configured.

- Free services may cold-start after inactivity, have ephemeral filesystems, and are subject to workspace-level quotas and provider policy changes. The app is stateless and stores no user scenarios; that does not imply an availability or latency guarantee.
- Bandwidth, build minutes, instance-hours, pricing, and overage rules can change and may be shared across a workspace. Review the current official Render pricing/limits and account Billing/Usage before relying on specific figures.
- This repository neither inspects nor changes payment methods, spend limits, or workspace billing. Do not infer that overages cannot be charged or that a plan change is authorised.

The published v0.1.0 service remains an intentionally limited portfolio demo: synthetic scenarios only, no public live NESO reading, physical dispatch, market trading, measured carbon savings, durable user state, or availability SLA. The v0.2.0 candidate adds a separate read-only regional display but is not deployed and does not change its scheduling behavior. If the existing service is confirmed to use the Free plan, its documented CPU/memory target, shared process-local rate limits, cold starts, instance restarts, and workspace quotas can make it slow or unavailable under concurrency, extended inactivity, or quota exhaustion. Do not present it as a continuously available production service.

References (provider documentation reviewed for this candidate; re-check for current terms):

- [Render pricing](https://render.com/pricing)
- [Render free-instance limitations](https://render.com/docs/free)
- [Render build pipeline and included minutes](https://render.com/docs/build-pipeline)
- [Render Blueprints and Auto Sync](https://render.com/docs/infrastructure-as-code)
- [Render web services and `PORT`](https://render.com/docs/web-services)
- [Render Blueprint specification](https://render.com/docs/blueprint-spec)
- [Render health checks](https://render.com/docs/health-checks)
- [Render rollbacks](https://render.com/docs/rollbacks)

## Configuration and deployment

`render.yaml` describes one Frankfurt Docker web service named `green-energy-dispatch-staging`, on `main`, with `plan: free`, simulation-only settings, collector disabled, and `autoDeployTrigger: "off"`; it declares no database, disk, worker, or Redis/Key Value instance. The existing public service is named `green-energy-dispatch-dashboard`, so this file is not a readback of that service's settings. Do not create or sync this differently named Blueprint during release packaging; it may propose an additional resource. The existing Dashboard is authoritative.

### Auto Deploy is not Blueprint Auto Sync

- **Service Auto Deploy** is specified as `off` in the checked-in Blueprint; that does not confirm the existing service setting.
- **Blueprint Auto Sync** is a separate Render Dashboard setting and is not controlled by `autoDeployTrigger`. The provider documents that a linked Blueprint can sync changed resources. Do not link or sync the differently named Blueprint as part of this documentation release.
- The existing service's Auto-Deploy and any Blueprint Auto Sync state must be read from the Dashboard; repository files cannot verify them. No Render setting or deployment is changed by this documentation work.

Before any separately approved service update (no such update is part of this release):

1. Verify that the target is the existing `green-energy-dispatch-dashboard` service. Compare its current active SHA with the owner-provided log record (`55ae4f274848ac3642e1e0bf804feba7f07b8659` from `main`); confirm the Compute Plan, Auto-Deploy setting, workspace usage, and current provider limits. Do not change billing, add a payment method, or alter plan settings.
2. Inspect any linked Blueprint and Auto Sync state. Do not connect or sync `render.yaml` as part of this work; it names a different service. If any setting differs from the approved release intent, stop and ask the owner rather than changing it.

For a future deployment, only after separate explicit owner approval:

1. Open the existing service directly; do not use **New → Blueprint** or create a second resource. Verify its repository, branch, plan, Auto-Deploy and deployment source before making any change.
2. If an approved manual deploy is authorised, confirm `main` still points to the reviewed commit. The service supplies `PORT` (default 10000); do not replace it with a fixed port. The application reads `PORT` at startup and binds to `0.0.0.0`.
3. Wait for the deployment and `/health` check, then read the deployed SHA from the provider Dashboard. If the service is on a Free plan, allow for cold starts; inspect logs without copying secrets into tickets or chat.
4. From a checkout containing the matching `examples/carbon_aware_ev_depot_demo.json`, run:

   ```bash
   python scripts/smoke_staging.py --base-url https://<service>.onrender.com
   ```

   The script refuses non-loopback plain HTTP and URL credentials. It verifies health, the root and SPA history fallback, that each built frontend asset is served from the same origin, a real deterministic synthetic evaluation and its provenance/claim flags, and that a POST to the NESO route returns 405 with `Allow: GET` without issuing a GET upstream request. Other protected energy routes and debug docs remain unavailable. This read-only-method probe does not verify that live NESO data is currently retrievable.
5. Record the deployed commit SHA, smoke output, health state, and any follow-up. Do not describe the synthetic scheduler as live-carbon-powered or as a trading service. The read-only panel only displays the public regional reading. A successful HTTP smoke is not a substitute for Apple Safari desktop/mobile visual acceptance.

The `CI and security` workflow builds the Docker image without publishing it and runs the simulation-only smoke against a loopback-only container constrained to **512 MB RAM and 0.1 CPU**, matching the intended Free-plan target documented for this candidate. A passing smoke is evidence for that bounded workload only; it is not a provider-plan verification, peak-memory measurement, load test, or guarantee against OOM under larger inputs or concurrent traffic. The workflow also runs frontend/backend checks and dependency/secret scans. Its Trivy checks are scoped to HIGH/CRITICAL findings; that is not an all-severity zero-vulnerability claim.

## Failure and rollback

If the deploy fails its health check, Render should keep routing to the prior healthy instance; inspect the deployment logs and do not retry by enabling automatic deploys. If the new version becomes unhealthy after activation:

1. In the Render service's **Deploys** page, choose the previous successful deploy and select **Rollback to this deploy**.
2. Wait for the rollback deploy and `/health` check to complete.
3. Re-run `scripts/smoke_staging.py` against the service URL and record the result.
4. Keep automatic deploys off until the cause is fixed and a new reviewed commit is deliberately deployed.

There are no application-data migrations or persisted user scenarios in this design, so code rollback does not require a database rollback. Keep the deploy's commit SHA and logs as evidence. If there is no prior successful deploy, use the provider dashboard to stop/disable public access while correcting the candidate; no destructive data recovery is needed.

## Separate follow-up

`frontend/src/components/ResultsPanel.tsx` has a known scroll-region accessibility follow-up. It is intentionally not part of this deployment candidate and does not support a claim of complete WCAG compliance.
