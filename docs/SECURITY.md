# Security findings and residual risk

## Container image assessment — 2026-10-10

Scans used Trivy 0.74.0 with its vulnerability database updated at 2026-10-10 01:03 UTC (next scheduled update: 2026-10-11 01:03 UTC). Scans covered OS and Python packages at HIGH and CRITICAL severities on both local Linux ARM64 and `linux/amd64` builds. The detailed Debian finding inventory below is from a fresh, no-cache rebuild of the existing `python:3.12-slim` image, not just the older saved local image scan.

| Image | Base OS | HIGH instances | Unique HIGH CVEs | CRITICAL | Python package findings |
|---|---|---:|---:|---:|---:|
| Existing `green-energy-dispatch-dashboard:local` | Debian 13.7 (`python:3.12-slim`) | 44 | 8 | 0 | 0 |
| Fresh rebuild using the same Debian base and `apt-get upgrade` | Debian 13.7 | 44 | 8 | 0 | 0 |
| Hardened `green-energy-dispatch-dashboard:security-hardened` | Alpine 3.24.2 (`python:3.12-alpine`) | 0 | 0 | 0 | 0 |

The fresh Debian rebuild pulled the then-current official `python:3.12-slim` image and ran `apt-get update` plus `apt-get upgrade` without build cache. Debian offered a `liblzma5` update, but it did not address the eight reported CVEs. The scanner reported no fixed version for any of the following Debian package findings:

| CVE | Severity | Affected Debian packages | Fixed version reported by Trivy |
|---|---|---|---|
| CVE-2026-76642 | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | None |
| CVE-2026-78408 | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | None |
| CVE-2026-78409 | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | None |
| CVE-2026-78410 | HIGH | bsdutils, libblkid1, liblastlog2-2, libmount1, libsmartcols1, libuuid1, login, mount, util-linux | None |
| CVE-2026-54369 | HIGH | libacl1 | None |
| CVE-2025-69720 | HIGH | libncursesw6, libtinfo6, ncurses-base, ncurses-bin | None |
| CVE-2026-16742 | HIGH | libsystemd0, libudev1 | None |
| CVE-2026-9538 | HIGH | perl-base | None |

The official `python:3.12-slim-bookworm` alternative was also checked as a base image. Its Debian 12.15 scan reported 57 HIGH/CRITICAL instances (55 HIGH, 2 CRITICAL) across 17 CVEs, with no fixed versions reported: CVE-2023-45853, CVE-2025-69720, CVE-2025-7458, CVE-2026-11822, CVE-2026-11824, CVE-2026-16742, CVE-2026-41992, CVE-2026-53613, CVE-2026-54369, CVE-2026-76642, CVE-2026-78408, CVE-2026-78409, CVE-2026-78410, CVE-2026-84782, CVE-2026-88647, CVE-2026-88648, and CVE-2026-9538. The affected base packages included zlib1g, libsqlite3-0, OpenSSL/GnuTLS, ncurses, systemd/udev, util-linux, ACL, gzip, and perl. This base was rejected: it has more reported findings than the current Debian 13 slim base, including two CRITICAL findings.

## Change and validation

The runtime base now uses the official Python 3.12 Alpine image. The image build applies `apk upgrade --no-cache` to pick up available repository updates and retains only the existing runtime dependencies. The candidate build used Python 3.12.15 and Alpine 3.24.2; compatible musllinux wheels were resolved for the native dependencies, and `pip check` passed.

The built image was run as UID/GID 10001. Its `/health` endpoint returned HTTP 200 and Docker marked its configured health check healthy. The repository's existing Docker Compose controls remain unchanged: read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and a constrained `/tmp` tmpfs. The CI workflow still reports all HIGH/CRITICAL findings and fails on fixable HIGH/CRITICAL findings; no CVEs are ignored or allow-listed.

The final Alpine scan found no vulnerability findings at any severity in OS or Python packages. This is scan evidence for the tested images and database snapshot, not a guarantee that the images have no vulnerabilities or that future database updates will remain clean. Rebuild and rescan images regularly; retain both CI Trivy steps and review new findings rather than suppressing them. Both ARM64 and `linux/amd64` builds were checked locally; CI provides the repository's independent build and security gate.

## Public simulation-only dashboard

The React/Vite dashboard is served by FastAPI as a limited public portfolio demo. Phase A is the deployed read-only NESO display; it remains isolated from the existing synthetic scheduler. Phase B1's forecast timeline and Phase B2a's forecast-backed core are merged code capabilities, while the Phase B2b forecast-evaluation API is a backend-only candidate and is **not deployed**. None of the forecast phases changes the existing Render service. The demo is not an authentication boundary or a production operations service.

- Local development uses Vite bound to `127.0.0.1`, proxying `/api` and `/health` to loopback FastAPI. The Docker demo instead serves the production Vite build and API from one origin. Neither mode requires permissive CORS; cross-origin deployment is unsupported.
- In simulation-only mode, the deployed public API exposes the existing synthetic `POST /api/v1/simulations/ev-depot/evaluate` and read-only Phase A `GET /api/v1/energy/carbon-intensity`. The Phase B2b candidate adds exact `POST /api/v1/simulations/ev-depot/forecast-evaluate`; it is not deployed. The candidate has a separate process-local rolling cap of 5 requests/minute by default (`FORECAST_EVALUATION_RATE_LIMIT_REQUESTS_PER_MINUTE`, bounded to 1–60) and also claims the shared NESO request bucket (`API_RATE_LIMIT_REQUESTS_PER_MINUTE`, default 60) used by the read-only NESO GET routes. Thus accepted forecast POSTs, including cache hits, share a per-process budget with NESO display/timeline reads. All buckets are shared by visitors, not per-IP or authenticated user, not coordinated across replicas, and reset on restart. There is no user authentication or authenticated operator identity. Request bodies are capped at 65,536 bytes by default (configurable); read-only NESO GET routes reject bodies and enforce the same configured maximum. The existing Render deployment remains unchanged.
- All NESO consumers use the fixed official HTTPS host `api.carbonintensity.org.uk`; no client request can choose an arbitrary external URL. The deployed Phase A `GET /api/v1/energy/carbon-intensity` accepts neither a caller-selected region nor URL. The Phase B1 read-only timeline route accepts a validated region ID but no URL. The B2b forecast-evaluation candidate accepts only the bounded depot scenario and an optional region assertion that must equal the server-configured region; it accepts no client forecast values, retrieval/decision timestamps, provenance, or results. Its exact new POST path is method-guarded. The shared NESO client disables redirects, requests identity encoding and rejects compressed responses before reading, bounds response bytes (64 KiB) and timeout (default 5 seconds, maximum 30 seconds), then validates the upstream shape and timeline evidence.
- The NESO cache is process-local and at most 30 seconds; it will not hold data past the validity/freshness bound. Fresh Phase A readings may be publicly cached for at most 30 seconds; stale readings and failures use `Cache-Control: no-store`. B2b uses the same cached validated timeline reader, rechecks freshness and full scenario coverage, and marks both available and unavailable advisory responses `no-store`. Missing, stale, malformed, region/unit-inconsistent, or out-of-coverage evidence returns HTTP 503 with no synthetic fallback and without raw upstream bodies. This limiter/cache is a basic abuse/resource control, not per-client or distributed protection and not proof of production readiness.
- The existing synthetic evaluator remains stateless, synthetic-only, and bounded to 96 intervals and 50 vehicles; it does not call NESO, access PostgreSQL, or start background work. The B2b candidate separately accepts at most 48 half-hour intervals and 50 vehicles, performs a read-only call through the validated NESO client, and schedules in memory; it does not persist results, enqueue jobs, or issue physical commands. Available evidence returns HTTP 200 even when a complete service schedule is not demonstrated; invalid scenarios return 422 and unavailable/invalid forecast evidence returns 503. Deterministic API tests use mocked upstream responses and verify GET is rejected on the POST-only candidate without a NESO call. The existing container smoke script continues to verify the deployed Phase A read-only route's method boundary without fetching live data; `/docs`, `/redoc`, and `/openapi.json` remain unavailable in simulation-only mode.
- There is no user authentication or authorisation system. The public demo is open to visitors; do not submit personal, operational, or confidential data.
- The browser imports only user-selected JSON files (bounded to 256 KiB in the UI) and exports scenario inputs as a local download. Submitted scenarios are not persisted by the evaluator or transmitted to unrelated services.
- Server validation details are mapped to form fields; non-validation server failures are displayed generically rather than exposing exception text.
- npm dependencies are pinned by the frontend lockfile. CI runs `npm audit`; update dependencies deliberately and review lockfile changes. The deployed dashboard is simulation-only and does not imply production-grade access control, availability, monitoring, or incident response.

## Other checks

The existing CI also runs `pip-audit` against declared runtime and development dependencies and Gitleaks against repository history and the working tree. Results are per-run evidence and should not be inferred from this local container scan.

Post-merge Actions run [40193307594](https://github.com/vlad12-k/green-energy-dispatch-dashboard/actions/runs/40193307594) passed `quality`, `frontend-quality`, `container`, and `secret-scan` on source commit `55ae4f274848ac3642e1e0bf804feba7f07b8659`. The workflow's Trivy checks cover CRITICAL/HIGH findings; a passing run is not evidence of zero findings at every severity. The older scan tables and candidate preflight below are historical evidence for their stated image/database snapshots, not a new scan of a future image.

## Historical release-candidate preflight — 2026-10-22

The following notes describe the pre-merge candidate state on 2026-10-22. They are retained as engineering history and are not substituted for the post-merge Actions run above or a future release-image scan.

- `npm audit --json` (all frontend dependencies) and `npm audit --omit=dev --json` (production dependencies) both reported zero vulnerabilities at every severity. `npm ls --depth=0` found no missing direct dependencies.
- `pip-audit --strict` reported no known vulnerabilities in 30 runtime requirements or 38 combined runtime/development requirements; `pip check` reported no broken requirements. The audit emitted warnings that invalid local HTTP-cache entries were ignored, then completed successfully with no known vulnerabilities.
- Gitleaks scanned the changed frontend source, E2E, public assets, HTML entry point, README, operator guide and design documents; it reported no leaks. This scoped local scan did not inspect `.env` files or claim to scan repository history.
- GitHub Actions run [39421372814](https://github.com/vlad12-k/green-energy-dispatch-dashboard/actions/runs/39421372814) succeeded on 2026-10-20 for the then-merged PR head `8d336fb`. Its newly built Alpine 3.24.2 image reported zero OS and Python-package vulnerabilities. That run predates the later deployment candidate and is not evidence for the image at `55ae4f2`.
- A fresh local Trivy scan of `green-energy-dispatch-dashboard:ci` did not complete before the tool timeout. The local Trivy database was last updated on 2026-10-17, so that interrupted attempt is not treated as a clean scan. Rebuild and rescan the image in CI for the follow-up release commit before deployment.
