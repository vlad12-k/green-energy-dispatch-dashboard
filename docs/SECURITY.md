# Security findings and residual risk

## Open container-image risk — 2026-10-10

The local Trivy 0.74.0 scan of `green-energy-dispatch-dashboard:local` found **44 HIGH finding instances across 8 unique vulnerability IDs** in Debian 13.7 OS packages. It found **0 CRITICAL findings** and no vulnerable application Python packages in the final image. The findings had no usable fixed version available to the scanner; a repeat scan with `--ignore-unfixed` reported zero HIGH/CRITICAL findings eligible for remediation.

This is an **open base-image security risk**, not a clean full-image report. Do not treat the passing fixable-vulnerability gate as proof that the image has no known vulnerabilities.

CI intentionally keeps two Trivy steps: one prints the complete HIGH/CRITICAL report without failing on findings that have no fix, and a second fails on fixable HIGH/CRITICAL findings. No CVE IDs are suppressed or allow-listed. Re-scan on each build and revisit this risk when Debian publishes fixes or the Python base image updates. Keep the non-root UID, dropped capabilities, read-only root filesystem, and `no-new-privileges` settings enabled.

## Other checks

At the same verification point, `pip-audit` reported no known vulnerabilities in declared runtime/development dependencies, and Gitleaks found no secrets in the repository history or working tree.
