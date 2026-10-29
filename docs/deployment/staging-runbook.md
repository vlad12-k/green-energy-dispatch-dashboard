# Render deployment runbook

**Status:** v0.2.0 Phase A is deployed. The release owner supplied a current Render Dashboard readback for the existing public service: **Live**, source `efbf1b2`, **Free** instance, matching application commit `efbf1b261bc542e6eed68543a49821511bf95b6a`. This is owner-verified provider evidence, not an independent Dashboard readback by this documentation pass; `/health` does not expose a revision, and Auto-Deploy remains unverified. Production smoke exited `0`; Safari desktop acceptance passed at a wide viewport, while mobile responsive acceptance is **NOT VERIFIED**. The `v0.2.0-alpha.1` tag is deferred until the documentation PR is merged and post-merge CI is green. This runbook update does not authorise a new resource, setting change, Blueprint sync, billing change, or deployment.

## Decision and topology

The intended topology uses one Render Docker web service for the React application and FastAPI API:

```text
Browser: https://green-energy-dispatch-dashboard.onrender.com
  GET / and client-side routes  ──> FastAPI serves frontend/dist/index.html
  GET /assets/*                ──> FastAPI serves built Vite assets
  POST /api/v1/simulations/ev-depot/evaluate ──> stateless synthetic evaluator
  GET /api/v1/energy/carbon-intensity      ──> deployed read-only configured NESO region
  POST /api/v1/energy/carbon-intensity     ──> 405; Allow: GET; no upstream request
  Other /api/* routes                     ──> 404 in SIMULATION_ONLY_MODE
```

The v0.2.0 Phase A route is deployed; the v0.1.0 release files and tag remain historical and unchanged. The production smoke deliberately does not issue an upstream GET: it verifies that a POST to the read-only route returns `405` with `Allow: GET`. A separate bounded acceptance GET to the public NESO endpoint returned HTTP `200` for region 13, London, but only a completed stale forecast; it was not a current actual reading. See [`../releases/v0.2.0-alpha.1.md`](../releases/v0.2.0-alpha.1.md) for the recorded response and Safari evidence.

The production Vite build uses an empty `VITE_API_BASE_URL`, so browser requests stay on the page's origin. This avoids CORS configuration, a second hostname, and browser credentials. The Render service provides an `onrender.com` hostname and managed TLS; no custom domain is required.

The EV depot evaluator remains stateless. The Blueprint does not provision PostgreSQL, a persistent disk, or a NESO credential. The deployed v0.2.0 API reads public NESO data through its fixed allowlisted HTTPS origin and writes neither the scenario nor the live response to persistence. Do not add a database unless a later, explicitly scoped feature introduces durable application state.

## Public-demo boundary

- Set `SIMULATION_ONLY_MODE=true`; allow only synthetic `POST /api/v1/simulations/ev-depot/evaluate` and read-only `GET /api/v1/energy/carbon-intensity` under `/api`.
- The NESO route uses only the fixed official HTTPS host, disables redirects, validates the response, caps response size at 64 KiB, and bounds the timeout to 30 seconds maximum (5 seconds by default). Its freshness-aware process cache lasts at most 30 seconds. Stale/error results are not publicly cached.
- Disable debug and OpenAPI docs (`DEBUG=false`).
- Bound the synthetic evaluator's request body to 65,536 bytes.
- Apply a separate 60-request rolling limit per application process for each public route. These in-memory limits are shared among visitors of a process, reset on restart, and are not per-IP or coordinated across replicas. Before broader promotion, add and verify an edge/per-client limiter and an abuse alert path.
- The evaluator accepts synthetic scenario input only and does not claim real-world emissions reductions. It does not persist submitted scenarios. The NESO GET is for display only and is not connected to the scheduler.
- No browser API token or authentication is required; anything embedded in frontend code is public, not a secret. Do not submit personal, operational, or confidential data.

## Provider cost and constraints

The release owner supplied a current Render Dashboard readback confirming the existing service uses the **Free** instance. The checked-in Blueprint also specifies `plan: free`, but names a different service and is not evidence of the existing service's settings. Provider documentation cited for this project described **0.1 CPU and 512 MB RAM** for a Free instance; these are documented limits, not measurements of the running instance. Free is a portfolio/demo tier, not a production availability target. Re-check current quotas, workspace usage, billing state, and provider terms before relying on any figure; this repository does not reveal whether a payment method or overage billing is configured.

- Free services may cold-start after inactivity, have ephemeral filesystems, and are subject to workspace-level quotas and provider policy changes. The app is stateless and stores no user scenarios; that does not imply an availability or latency guarantee.
- Bandwidth, build minutes, instance-hours, pricing, and overage rules can change and may be shared across a workspace. Review the current official Render pricing/limits and account Billing/Usage before relying on specific figures.
- This repository neither inspects nor changes payment methods, spend limits, or workspace billing. Do not infer that overages cannot be charged or that a plan change is authorised.

The deployed v0.2.0 Phase A service remains an intentionally limited portfolio demo: synthetic scheduling, a separate read-only public NESO display, no physical dispatch, market trading, measured carbon savings, durable user state, or availability SLA. Its confirmed Free instance, shared process-local rate limits, cold starts, restarts, and workspace quotas can constrain availability under concurrency, extended inactivity, or quota exhaustion. Do not present it as a continuously available production service. The live reading is display-only and does not change scheduler behaviour.

References (re-check current provider terms before any future operation):

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

1. Verify that the target is the existing `green-energy-dispatch-dashboard` service. Compare its current active SHA with the owner-provided closeout readback (`efbf1b261bc542e6eed68543a49821511bf95b6a` from `main`); refresh the current plan, Auto-Deploy setting, workspace usage, and provider limits in the Dashboard. The supplied readback confirms the Free instance, but not Auto-Deploy. Do not change billing, add a payment method, or alter plan settings.
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
5. Record the deployed commit SHA, smoke output, health state, and any follow-up. Do not describe the synthetic scheduler as live-carbon-powered or as a trading service. The read-only panel only displays the public regional reading. Desktop Safari simulation and live-data boundary acceptance passed at a wide viewport; mobile responsive acceptance is **NOT VERIFIED**. See [`../releases/v0.2.0-alpha.1.md`](../releases/v0.2.0-alpha.1.md). A successful HTTP smoke is not a substitute for mobile visual acceptance.

The `CI and security` workflow builds the Docker image without publishing it and runs the simulation-only smoke against a loopback-only container constrained to **512 MB RAM and 0.1 CPU**, approximating the documented Free-instance limits. A passing smoke is evidence for that bounded workload only; it is not a provider-plan verification, peak-memory measurement, load test, or guarantee against OOM under larger inputs or concurrent traffic. The workflow also runs frontend/backend checks and dependency/secret scans. Its Trivy checks are scoped to HIGH/CRITICAL findings; that is not an all-severity zero-vulnerability claim.

## Failure and rollback

If the deploy fails its health check, Render should keep routing to the prior healthy instance; inspect the deployment logs and do not retry by enabling automatic deploys. If the new version becomes unhealthy after activation:

1. In the Render service's **Deploys** page, choose the previous successful deploy and select **Rollback to this deploy**.
2. Wait for the rollback deploy and `/health` check to complete.
3. Re-run `scripts/smoke_staging.py` against the service URL and record the result.
4. Keep automatic deploys off until the cause is fixed and a new reviewed commit is deliberately deployed.

There are no application-data migrations or persisted user scenarios in this design, so code rollback does not require a database rollback. Keep the deploy's commit SHA and logs as evidence. If there is no prior successful deploy, use the provider dashboard to stop/disable public access while correcting the candidate; no destructive data recovery is needed.

## Separate follow-up

`frontend/src/components/ResultsPanel.tsx` has a known scroll-region accessibility follow-up. It remains outside v0.2.0 Phase A and does not support a claim of complete WCAG compliance.
