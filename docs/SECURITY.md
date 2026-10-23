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

## Operator dashboard prototype

The React/Vite dashboard is a local-development client, not a production security boundary.

- Vite binds to `127.0.0.1` and proxies `/api` and `/health` to `127.0.0.1:8000`. FastAPI has no permissive CORS policy added for this feature. Cross-origin deployment is unsupported; do not add wildcard origins.
- The API simulation endpoint is stateless, synthetic-only and bounded to 96 intervals and 50 vehicles. It does not call NESO, read/write PostgreSQL, or start background work.
- There is no user authentication or authorisation system. Keep both development servers on loopback and do not expose them to untrusted networks.
- The browser imports only user-selected JSON files (bounded to 256 KiB in the UI) and exports scenario inputs as a local download. It does not persist scenarios or transmit them to external services.
- Server validation details are mapped to form fields; non-validation server failures are displayed generically rather than exposing exception text.
- npm dependencies are pinned by the frontend lockfile. CI runs `npm audit`; update dependencies deliberately and review lockfile changes. The production build is not deployed or served by FastAPI in this milestone.

## Other checks

The existing CI also runs `pip-audit` against declared runtime and development dependencies and Gitleaks against repository history and the working tree. Results are per-run evidence and should not be inferred from this local container scan.

## Release-candidate preflight — 2026-10-22

- `npm audit --json` (all frontend dependencies) and `npm audit --omit=dev --json` (production dependencies) both reported zero vulnerabilities at every severity. `npm ls --depth=0` found no missing direct dependencies.
- `pip-audit --strict` reported no known vulnerabilities in 30 runtime requirements or 38 combined runtime/development requirements; `pip check` reported no broken requirements. The audit emitted warnings that invalid local HTTP-cache entries were ignored, then completed successfully with no known vulnerabilities.
- Gitleaks scanned the changed frontend source, E2E, public assets, HTML entry point, README, operator guide and design documents; it reported no leaks. This scoped local scan did not inspect `.env` files or claim to scan repository history.
- GitHub Actions run [39421372814](https://github.com/vlad12-k/green-energy-dispatch-dashboard/actions/runs/39421372814) succeeded on 2026-10-20 for the already-merged PR head `8d336fb`. Its newly built Alpine 3.24.2 image reported zero OS and Python-package vulnerabilities. That run predates this uncommitted frontend/documentation candidate; the current diff does not change the Dockerfile or dependency manifests.
- A fresh local Trivy scan of `green-energy-dispatch-dashboard:ci` did not complete before the tool timeout. The local Trivy database was last updated on 2026-10-17, so that interrupted attempt is not treated as a clean scan. Rebuild and rescan the image in CI for the follow-up release commit before deployment.
