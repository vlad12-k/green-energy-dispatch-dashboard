# Architecture

## Product boundary

Green Energy Dispatch Dashboard evaluates data-driven **simulated** dispatch recommendations. It is a demonstrator for responsible AI and digital-energy solution design.

It does **not** connect to an electricity-market participant, an exchange, a broker, a National Grid control system, or a physical device. It does not place trades or issue control commands.

## C4-style context

```text
Grid-data provider ──> Data ingestion ──> Evidence store
                                            │
Operator <── dashboard <── API/policy <─────┼── forecast service
                                            │
                                      telemetry/metrics ──> Grafana
```

### Containers (target state)

| Container | Responsibility | Status |
|---|---|---|
| FastAPI service | API boundary, validation, carbon policy, policy explanation | implemented first slice |
| Ingestion worker | fetch data, preserve source/timestamp/hash, freshness validation | planned |
| PostgreSQL/TimescaleDB | application state, aggregated time series, provenance references | planned |
| ML training job | train and evaluate reproducible baseline and later neural models | planned |
| React UI | explain decisions, show provenance and scenario replay | planned |
| Prometheus/Grafana | technical metrics, SLOs and operational dashboards | planned |

## Decision flow

1. A caller supplies a carbon-intensity value or, later, an evidence-backed ingestion record.
2. The API validates the value and sends it to the policy service.
3. The policy produces `allow`, `defer`, or `block` with a human-readable reason.
4. The API returns the decision. It does not execute the decision.
5. A future version records provenance, evaluation version, timestamp, and policy version.

## Current carbon policy

```text
≤150 gCO₂e/kWh     allow
151–300 gCO₂e/kWh  defer
>300 gCO₂e/kWh     block
```

The thresholds are illustrative. They must not be presented as an operational recommendation until validated against a defined carbon source, commercial objective, safety analysis, and UK-market constraints.

## Key architecture decisions

### Explainable policy before opaque optimisation
The first control is a deterministic policy because its thresholds and reason are reviewable. A forecasting model may estimate future signals, but a forecast must not obscure the enforcement rule.

### Data provenance before model sophistication
A credible portfolio project needs a source, timestamp, freshness result, input hash, and historical replay path before it needs an LSTM. This prevents a dashboard from making untraceable claims.

### Baseline before neural network
The forecasting work should compare seasonal-naïve and gradient-boosted baselines with a temporal holdout. PyTorch LSTM work proceeds only if it improves evaluated metrics under the same split.

### No live execution
Integration with a market or asset would add regulatory, safety, security, and commercial risks that are inappropriate for a portfolio demonstrator. The product remains simulation-only.

## Non-functional requirements

- **Reproducibility:** a known historical fixture replays to the same result.
- **Validation:** malformed, stale, and missing evidence causes a visible failure.
- **Observability:** request/error/decision metrics are exposed without sensitive source data.
- **Security:** no secrets in Git; least-privilege runtime configuration; dependency scanning in CI.
- **Accessibility:** dashboard decisions communicate state without colour alone.

## Delivery evidence for recruiters

The final repository should include: ADRs, C4 diagrams, API contract, test coverage, model card, data-source record, screenshots of monitoring, a deployed demo with no credentials required, and a short two-minute walkthrough video.