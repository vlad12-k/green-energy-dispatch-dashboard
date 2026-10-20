# ADR-001: React/Vite operator dashboard with a same-origin API proxy

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

The EV-depot scheduler and its scenario contract are implemented in Python. The first operator-facing dashboard must provide interactive editing and visual feedback while keeping Python as the only authoritative scheduling, feasibility, and carbon-proxy engine. The existing FastAPI application must remain usable without a frontend. The first delivery is local-only and must not require credentials, a database, cloud hosting, or broad CORS configuration.

## Decision

Build a focused React and TypeScript single-page application with Vite. Run the API and frontend as separate loopback processes during development. Vite proxies `/api` and `/health` to the loopback FastAPI server, so the browser sends same-origin requests and FastAPI does not need CORS middleware for this workflow.

The dashboard sends validated `CarbonAwareDepotScenario` JSON to the versioned simulation endpoint and renders the returned result. TypeScript API models are generated from `app.openapi()` with `openapi-typescript`. A small set of native SVG charts includes accessible labels and expandable numerical tables; no charting framework or component library is added. The frontend is not implicitly mounted by FastAPI, and this ADR does not establish a production serving or deployment design.

## Alternatives considered

1. **Serve the SPA from FastAPI immediately.** Rejected for this milestone: it couples frontend build output to backend packaging and changes API application lifecycle/deployment without a production-serving requirement. The backend remains independently operational.
2. **Use a separate frontend origin with CORS.** Rejected for local development: it adds cross-origin policy and configuration without benefit for the demonstration.
3. **Use a server-rendered React framework or separate frontend service.** Rejected: the dashboard is a local scenario editor with a single synchronous API call; server rendering and another runtime would add operational complexity without a requirement.
4. **Use a charting library.** Rejected for the first version: the required time-aligned plots are bounded and small, and native SVG plus data tables provide a smaller dependency surface and explicit accessibility behaviour.

## Consequences and trade-offs

- React state owns editable UI state only; the Python scheduler remains authoritative for schedule and proxy calculations.
- Generated OpenAPI types reduce hand-maintained contract drift, but frontend build/type generation requires the Python runtime dependencies to be installed.
- Local API requests are same-origin through Vite. Cross-origin hosting remains unsupported until an explicit origin allowlist and deployment design are reviewed.
- The frontend and backend have separate local commands. No production static serving, API authentication, or deployment behavior is implied.
- React, Vite, TypeScript, ESLint, Vitest/Testing Library, Playwright, and OpenAPI type generation are project-local, exact-version dependencies recorded in `frontend/package-lock.json`.
- Native SVG avoids a chart-library dependency, but chart semantics and the accompanying tabular alternatives remain maintained by this repository.
