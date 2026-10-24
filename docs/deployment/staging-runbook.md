# Staging deployment runbook

**Status:** configuration and code only. No Render service, DNS record, deployment, or paid resource has been created. Creating a service requires separate approval.

## Decision and topology

Use one Render Docker web service for the React application and FastAPI API:

```text
Browser: https://<service>.onrender.com
  GET / and client-side routes  ──> FastAPI serves frontend/dist/index.html
  GET /assets/*                ──> FastAPI serves built Vite assets
  POST /api/v1/simulations/ev-depot/evaluate ──> stateless synthetic evaluator
  Other /api/* routes           ──> 404 in SIMULATION_ONLY_MODE
```

The production Vite build uses an empty `VITE_API_BASE_URL`, so browser requests stay on the page's origin. This avoids CORS configuration, a second hostname, and browser credentials. The Render service provides an `onrender.com` hostname and managed TLS; no custom domain is required.

The EV depot evaluator is stateless. The Blueprint does not provision PostgreSQL, a persistent disk, or a NESO credential. Do not add a database unless a later, explicitly scoped feature introduces durable application state.

## Public-demo boundary

- Set `SIMULATION_ONLY_MODE=true`; only `POST /api/v1/simulations/ev-depot/evaluate` is exposed under `/api`.
- Disable debug and OpenAPI docs (`DEBUG=false`).
- Bound each request body to 65,536 bytes.
- Apply a 60-request rolling limit per application process. This deliberately simple in-memory limit is shared by visitors of that process, resets on restart, and is not a per-IP quota. It is suitable only for a controlled portfolio demo; before broader promotion, add and verify an edge/per-client limiter and an abuse alert path.
- The evaluator accepts synthetic scenario input only and does not claim real-world emissions reductions. It does not persist submitted scenarios.
- Do not add a browser API token: anything embedded in frontend code is public, not a secret.

## Provider cost and constraints

The Blueprint uses Render's **Free** web-service compute plan (`plan: free`): **0.1 CPU and 512 MB RAM**, with no compute charge. This is a portfolio/demo tier, not a production availability target.

- Render spins a Free web service down after **15 minutes without inbound traffic**. The next HTTP request or WebSocket connection wakes it; Render says this takes **about one minute** and may show a loading page. This is approximate cold-start guidance, not a latency guarantee.
- Render grants **750 Free instance-hours per workspace per calendar month**. Spun-down services do not consume these hours. If the workspace exhausts the allowance, Render suspends all its Free web services until the next month. Other Free services in that workspace share the allowance.
- The filesystem is ephemeral. Changes are lost on spin-down/redeploy, and Render may restart a Free instance at any time. This app is stateless and stores no user scenarios, so it does not rely on local persistence.
- Outbound bandwidth and build-pipeline minutes are **workspace-level, shared limits**, not unlimited entitlements attached to this service. Render's current pricing page lists, for example, Hobby at **5 GB outbound bandwidth and 500 standard pipeline minutes per month**; Pro and Scale allocations differ. Check the actual workspace's Billing/Usage page before provisioning.
- The current pricing page lists supplementary usage at **$0.15 per GB** of outbound bandwidth and **$5 per 1,000** standard pipeline minutes after the included amount. If a payment method is on file, overage billing may apply. Without a payment method, exceeding outbound bandwidth suspends Free services for the rest of that month; exhausting pipeline minutes disables new builds for the rest of the month while existing deployed artifacts keep running. Exhausting Free instance-hours also suspends Free services until the next month.
- Render's Free service creation does not require adding a payment method. This repository does not add one. Existing workspace billing configuration is not visible from this repository, so inspect it rather than assuming no payment method is attached or that overages cannot be billed.

This remains an intentionally limited portfolio demo: synthetic scenarios only, no live NESO data, physical dispatch, market trading, measured carbon savings, durable user state, or availability SLA. The 0.1 CPU limit, shared process-wide rate limit, cold starts, instance restarts, and monthly workspace quotas can make the service slow or unavailable under concurrency, extended inactivity, or quota exhaustion. Do not present it as a continuously available production service.

References (provider documentation, checked for this candidate):

- [Render pricing](https://render.com/pricing)
- [Render free-instance limitations](https://render.com/docs/free)
- [Render build pipeline and included minutes](https://render.com/docs/build-pipeline)
- [Render Blueprints and Auto Sync](https://render.com/docs/infrastructure-as-code)
- [Render web services and `PORT`](https://render.com/docs/web-services)
- [Render Blueprint specification](https://render.com/docs/blueprint-spec)
- [Render health checks](https://render.com/docs/health-checks)
- [Render rollbacks](https://render.com/docs/rollbacks)

## Configuration and deployment

`render.yaml` declares exactly one Frankfurt Docker web service, `green-energy-dispatch-staging`, on `main` with `plan: free`. It declares no database, disk, worker, Redis/Key Value instance, or other paid resource. The simulation-only settings remain enabled, the collector remains disabled, and `autoDeployTrigger: "off"` remains set.

### Auto Deploy is not Blueprint Auto Sync

- **Service Auto Deploy** is controlled here by `autoDeployTrigger: "off"`. It prevents ordinary commits from automatically deploying new application code to this service.
- **Blueprint Auto Sync** is a separate Render Dashboard setting. Render documents that it is enabled by default for a linked Blueprint and that pushes modifying its YAML can deploy added or changed resources. It is not disabled by `autoDeployTrigger`. Set the Blueprint's **Settings → Auto Sync → No** and use **Manual Sync** only when deliberately reviewing a Blueprint change.
- A GitHub merge does not itself call Render or create a service when no Blueprint is connected. However, if a Blueprint is already linked to this repository/branch with Auto Sync enabled, merging a changed `render.yaml` can sync resources. Verify the Dashboard state before merge; it is not observable from this repository.

Before merge or provisioning:

1. In the Render Dashboard, inspect the target workspace's **Billing/Usage** page and verify its included outbound bandwidth, pipeline minutes, current usage, payment-method state, and spend limits. Do not add a payment method. The owner must decide whether the potential loss of service/build capability at free quotas is acceptable.
2. Check whether a Blueprint is already connected to this repository and `main`. If one exists, inspect its resources and set **Auto Sync = No** before merging or manually syncing. Do not assume the account has no Blueprint or no billing method.

After merge and separate human deployment approval:

1. For a new staging resource, use **New → Blueprint**, connect this repository, select `main`, and review the proposed changes before applying them. Confirm the plan is Free and that exactly one web service will be created; reject any database, disk, worker, Redis/Key Value, or paid resource. Applying/Deploying the Blueprint is the explicit provisioning/deployment action and is outside the changes made in this PR.
2. Keep service Auto Deploy off and Blueprint Auto Sync set to No. The service supplies `PORT` (default 10000); do not replace it with a fixed port. The application reads `PORT` at startup and binds to `0.0.0.0`.
3. Confirm the first deployment uses the reviewed `main` commit, then wait for Render's `/health` check to pass. Allow for Free-tier cold starts; inspect build/runtime logs without copying secrets into tickets or chat.
4. From a checkout containing the matching `examples/carbon_aware_ev_depot_demo.json`, run:

   ```bash
   python scripts/smoke_staging.py --base-url https://<service>.onrender.com
   ```

   The script refuses non-loopback plain HTTP and URL credentials. It verifies health, the root and SPA history fallback, that each built frontend asset is served from the same origin, a real deterministic synthetic evaluation and its provenance/claim flags, and that live energy routes and debug docs are unavailable.
5. Record the deployed commit SHA, smoke output, health state, and any follow-up. Do not describe this as a live-carbon or trading service.

The `CI and security` workflow builds the Docker image without publishing it and runs the simulation-only smoke against a loopback-only container constrained to **512 MB RAM and 0.1 CPU**, matching the Free service limits for a startup and one synthetic scenario. A passing smoke is evidence for that bounded workload only; it is not a peak-memory measurement, load test, or guarantee against OOM under larger inputs or concurrent traffic. The workflow also runs frontend/backend checks and dependency/secret scans. Its Trivy checks are scoped to HIGH/CRITICAL findings; that is not an all-severity zero-vulnerability claim.

## Failure and rollback

If the deploy fails its health check, Render should keep routing to the prior healthy instance; inspect the deployment logs and do not retry by enabling automatic deploys. If the new version becomes unhealthy after activation:

1. In the Render service's **Deploys** page, choose the previous successful deploy and select **Rollback to this deploy**.
2. Wait for the rollback deploy and `/health` check to complete.
3. Re-run `scripts/smoke_staging.py` against the service URL and record the result.
4. Keep automatic deploys off until the cause is fixed and a new reviewed commit is deliberately deployed.

There are no application-data migrations or persisted user scenarios in this design, so code rollback does not require a database rollback. Keep the deploy's commit SHA and logs as evidence. If there is no prior successful deploy, use the provider dashboard to stop/disable public access while correcting the candidate; no destructive data recovery is needed.

## Separate follow-up

`frontend/src/components/ResultsPanel.tsx` has a known scroll-region accessibility follow-up. It is intentionally not part of this deployment candidate and does not support a claim of complete WCAG compliance.
