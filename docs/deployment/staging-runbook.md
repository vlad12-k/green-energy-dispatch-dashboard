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

Render's current pricing page lists the `0.5c-512mb` web-service compute plan at **$7/month** (0.5 CPU, 512 MB RAM). This is compute pricing, not a guaranteed total invoice: workspace plan, bandwidth/build overages, taxes, and future provider pricing may affect the amount. The listed Hobby allowance is 5 GB monthly bandwidth (then $0.15/GB) and 500 standard build minutes (then $5 per 1,000 minutes); confirm the live dashboard pricing before provisioning.

The free web-service plan is not recommended for a portfolio demo that should feel continuously available: it has 512 MB RAM, spins down after 15 minutes without inbound traffic and takes about a minute to wake, has 750 free instance-hours per month, may restart, and loses local filesystem changes on spin-down/redeploy. This application is stateless, so the filesystem limitation does not require a database workaround, but the sleep/wake behaviour is visible to visitors.

References (provider documentation, checked for this candidate):

- [Render pricing](https://render.com/pricing)
- [Render free-instance limitations](https://render.com/docs/free)
- [Render web services and `PORT`](https://render.com/docs/web-services)
- [Render Blueprint specification](https://render.com/docs/blueprint-spec)
- [Render health checks](https://render.com/docs/health-checks)
- [Render rollbacks](https://render.com/docs/rollbacks)

## Configuration and deployment

`render.yaml` is a Blueprint proposal for a Frankfurt `0.5c-512mb` Docker web service, built from `main`. It explicitly sets `autoDeployTrigger: off`; merging code will not automatically deploy it. The service is not provisioned by this repository change.

After the PR is reviewed/merged and a separate deployment approval is given:

1. In Render, connect the repository and create/sync the Blueprint. Review the service name, Frankfurt region, plan, environment variables, and expected charges before confirming creation.
2. Keep auto-deploy disabled. The Render service supplies `PORT` (default 10000); do not replace it with a fixed port. The application reads `PORT` at startup and binds to `0.0.0.0`.
3. Confirm the first deploy uses the reviewed `main` commit, then wait for Render's `/health` check to pass. Inspect build/runtime logs without copying secrets into tickets or chat.
4. From a checkout containing the matching `examples/carbon_aware_ev_depot_demo.json`, run:

   ```bash
   python scripts/smoke_staging.py --base-url https://<service>.onrender.com
   ```

   The script refuses non-loopback plain HTTP and URL credentials. It verifies health, the root and SPA history fallback, that each built frontend asset is served from the same origin, a real deterministic synthetic evaluation and its provenance/claim flags, and that live energy routes and debug docs are unavailable.
5. Record the deployed commit SHA, smoke output, health state, and any follow-up. Do not describe this as a live-carbon or trading service.

The `CI and security` workflow builds the Docker image without publishing it, runs the same smoke against a loopback-only container, runs frontend/backend checks and dependency/secret scans, and scans the final image. Its Trivy checks are scoped to HIGH/CRITICAL findings; that is not an all-severity zero-vulnerability claim.

## Failure and rollback

If the deploy fails its health check, Render should keep routing to the prior healthy instance; inspect the deployment logs and do not retry by enabling automatic deploys. If the new version becomes unhealthy after activation:

1. In the Render service's **Deploys** page, choose the previous successful deploy and select **Rollback to this deploy**.
2. Wait for the rollback deploy and `/health` check to complete.
3. Re-run `scripts/smoke_staging.py` against the service URL and record the result.
4. Keep automatic deploys off until the cause is fixed and a new reviewed commit is deliberately deployed.

There are no application-data migrations or persisted user scenarios in this design, so code rollback does not require a database rollback. Keep the deploy's commit SHA and logs as evidence. If there is no prior successful deploy, use the provider dashboard to stop/disable public access while correcting the candidate; no destructive data recovery is needed.

## Separate follow-up

`frontend/src/components/ResultsPanel.tsx` has a known scroll-region accessibility follow-up. It is intentionally not part of this deployment candidate and does not support a claim of complete WCAG compliance.
