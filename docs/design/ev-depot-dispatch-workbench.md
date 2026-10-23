# EV Depot Dispatch Workbench

**Status:** Acceptance candidate; not approved as a final release. This replaces the rejected light dashboard direction documented in `musemind-ev-dispatch-direction.md`.

## References inspected

### Musemind — Wind Power Energy Management Dashboard

[Approved Behance project](https://www.behance.net/gallery/233660825/Wind-power-energy-management-dashboard)

The inspected screen is presented in a monitor mock-up: a pale neutral interface with compact pill navigation, a large left-aligned heading, a dominant turbine/nacelle visual with a floating environmental-information panel, and a lower row of overview, wind-energy, wind/nacelle, and temperature modules. The adaptation takes only the disciplined hierarchy, concise technical labelling, differentiated data modules, and restrained status treatment. It does not reuse the turbine/equipment imagery, wind metrics, brand, or proprietary screen composition.

### Selected Adobe Stock asset — Asset ID 401118151

[Sales report dashboard panel user interface with dark mode](https://stock.adobe.com/uk/images/sales-report-dashboard-panel-user-interface-with-dark-mode/401118151)

- **Verified preview:** [Adobe Stock preview image](https://as2.ftcdn.net/jpg/04/01/11/81/1000_F_401118151_YdSu8HPBWR8lXbFLTt8RTjCVCGbusuMG.jpg)
- **Source files shown by Adobe:** editable AI/EPS vector and JPEG. The public preview itself is a watermarked JPEG.
- **Visible composition:** a dark, three-zone sales-report interface, with a compact navigation/profile area, grouped report modules, a circular comparison graphic, bar/area chart, and a larger daily chart. The example data and sales semantics are not used.
- **Why it is useful:** the preview demonstrates a coherent hierarchy for separating a persistent control surface from a larger analytical workspace, and shows multiple chart treatments in one system. These patterns map to scenario inputs, a strategy delta, interval values, and returned schedule detail without importing unsupported metrics.
- **Licence boundary:** the item page displayed a Standard licence option; Adobe's published Standard terms limit reproduction to 500,000 copies in most media and prohibit stand-alone redistribution and resale products whose primary value is the asset. Adobe's terms also document Enhanced and Extended licence categories. The asset's account-specific price/credit cost, entitlement, plan inclusion, and licence history were not verified. No asset was bought, licensed, downloaded, embedded, or copied into this implementation.
- **Authenticated-context limitation:** Safari contained the exact Stock item tab, but its Adobe Home tab reported “You have been signed out”; the Stock page displayed a free-trial offer. No sign-in, trial, library save, download, or licence action was taken. The public item preview and licensing terms were inspected; current account entitlement and account-specific cost remain unverified.

## Implementation direction

- Replace the top pill navigation with a persistent, numbered workspace rail; keep the run action in a distinct top command bar.
- Use a compact page heading and a split workbench: scenario editor dock on the left, analytical output canvas on the right.
- Make the strategy delta the comparison anchor; show the two backend values as separate labelled lanes and retain the equal-service eligibility statement.
- Give the synthetic interval profile and API evaluation trace dedicated full-width chart panels. Put the two detailed schedule workspaces in a full-width lower band rather than inside the analysis column.
- Use a midnight/slate foundation, acid-lime action/delta accent, violet baseline, and teal heuristic/data accent. Keep technical identifiers and units tabular; reserve warning colour for incomplete or failed states.
- Reflow the navigation to a compact mobile rail and stack input, analysis, and schedule sections without hiding controls or permitting page-level horizontal overflow.

## Data and behaviour boundaries

The React presentation layer may change; scenario configuration/validation, vehicle and availability inputs, synthetic profile provenance, JSON import/export, API contracts, FastAPI simulator mathematics, strategy definitions, result comparison, and units remain the existing application behaviour. Interval charts render one bar per supplied interval and do not interpolate. The evaluation trace describes the application/API boundary; it is not a physical depot or grid diagram. Synthetic proxy values are not measured emissions or savings claims.

## Final QA evidence

The final visual review used the actual Chrome-rendered application at 1440×900, full-page (1440×1959), and mobile 390×844, including a mobile results view. The refreshed captures are `docs/screenshots/ev-depot-workbench-after-1440x900.png`, `docs/screenshots/ev-depot-workbench-after-full-page.png`, `docs/screenshots/ev-depot-workbench-after-390x844.png`, and `docs/screenshots/ev-depot-workbench-after-mobile-results-390x844.png`. The comparison capture of the rejected interface is `docs/screenshots/musemind-ev-dispatch-after-1440x900.png`.

Verified locally:

- Frontend: Vitest **30 passed** (6 files); ESLint; TypeScript typecheck; production build; Playwright **5 passed**. The browser flow exercised the FastAPI evaluation endpoint, synthetic result values and units, JSON export, keyboard focus visibility, 320/390 px layout and page-overflow checks, legibility of chart text/units/keys, keyboard focus for labelled horizontally scrollable data tables, reduced-motion CSS, flow-map visibility at 760/761, 800, 1120/1121, 1200, and 1280/1281 px, and zero browser console or uncaught page errors. WCAG AA 4.5:1 contrast checks cover the small rail labels and version label on the rail.
- Python: project-local Python **3.12.13** environment; `pip check`; Ruff; `alembic upgrade head`; `alembic check`; full suite **248 passed**, **93.48%** coverage against the 85% gate. PostgreSQL integration tests used a short-lived PostgreSQL 17 container backed by tmpfs; it was stopped and removed after the run. The repository's existing database service was not used for tests.
- The previously incomplete project `.venv` lacked declared runtime packages (`SQLAlchemy`, `alembic`, and `psycopg`). Installing the pinned `requirements-dev.txt` into that project-local environment resolved collection; dependency declarations were not changed and global Python was not modified.

## Acceptance limits and release status

The interface is locally verified as a synthetic simulation workbench, not a production dispatch/control product. The browser/API values are computed by FastAPI from synthetic example inputs; they are not measured emissions or savings claims. The API trace does not represent a physical grid or depot topology. No live NESO signal is used for these simulations, no physical actuation/trading is supported, and no deployment or production hosting has been configured.

Browser evidence is from Chrome/Playwright only. Keyboard focus and selected contrast pairs are exercised, but there has been no complete WCAG audit, screen-reader test, or Safari/Firefox matrix. Adobe Stock Asset 401118151 was viewed only through its public preview; the existing Adobe session was signed out, so account entitlement and account-specific cost remain unknown. No asset was licensed or downloaded.

These results establish a locally tested portfolio candidate, not deployment readiness. During delivery preparation, the original worktree's uncommitted changes and historical screenshots were preserved; the UI follow-up was isolated in a new branch from `main`. The release-preparation inventory contained 21 PNGs: four current workbench captures listed above, one historical/rejected-direction comparison capture, and earlier iteration artifacts. `operator-dashboard-dark-redesign-full-page.png` and `operator-dashboard-reference-comparison-before-full-page.png` were byte-identical. The follow-up refreshed the four current workbench captures and retained the rejected-direction comparison for context; other historical screenshots were neither deleted nor included. Hosting, environment-specific configuration, deployment smoke tests, and rollback/operations evidence are required before deployment.
