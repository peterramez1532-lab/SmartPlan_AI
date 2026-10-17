# SmartPlan AI — Industrial Production Planning PoC

Local-first production planning and factory workflow prototype for the IB-02 brief. It supports order intake, machine setup, proposed scheduling, disruption what-if analysis, configurable multi-factory product workflows, workforce records, historical machine performance, downtime logs, execution feedback, KPI baselines, an interactive synthetic demo dataset, and an optimization-grounded production advisor.

> **Important:** this is a PoC/MVP, not a validated factory-control system. Use verified factory data, review every proposal with production managers, and keep machine control/manual execution outside the application. No IB-02-specific rates or workflows are assumed in the seed data.

## Windows quick start (no Docker)

Requirements: Windows 10/11, Python 3.11+, PowerShell. Internet is needed to install Python packages for the first run.

1. Extract `SmartPlan_AI.zip` to a writable folder, e.g. `C:\Projects\SmartPlan_AI`.
2. Open that folder in VS Code or PowerShell.
3. If PowerShell blocks scripts, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`.
4. Run `./run.ps1` from the project folder (equivalent PowerShell command: ` .\run.ps1 ` without the surrounding spaces).
5. Open `http://127.0.0.1:8000`. API documentation is at `http://127.0.0.1:8000/docs`.
6. Stop the server with Ctrl+C. Rerun `./run.ps1` to start again.

The launcher creates `.venv`, installs requirements, creates `.env` only if it is missing, and initializes missing database tables. It does not delete or overwrite the database. Docker and Docker Desktop are not required.

## Configuration and data safety

Default database: `data/smartplan.db` (SQLite). `.env.example` contains:

```env
APP_NAME=SmartPlan AI
DATABASE_URL=sqlite:///./data/smartplan.db
MAX_UPLOAD_MB=10
```

PostgreSQL can be configured using a separately provisioned PostgreSQL server and a suitable `DATABASE_URL`. This project does not create or manage a PostgreSQL server. New tables are created additively; there is not yet a migration framework. Back up `data/smartplan.db` before updating the project and never delete it as a troubleshooting step.

## Current features

### Interactive demo mode
- The Overview page offers a one-click synthetic scenario with 8 mesh orders, 4 machines and illustrative performance factors.
- Demo loading is refused if the workspace already contains orders or machines; existing data is never overwritten. The sample data is not IB-02 factory data.

### Orders, machines and imports
- Create, list, search, update status, and delete production orders with validation and duplicate prevention.
- Import XLSX, XLS and CSV with preview, row validation, duplicate detection, and confirm-to-import.
- Configure machine type, capacity/day, shift hours, availability and maintenance dates.
- Record actual daily output and downtime.
- Export orders to CSV and inspect API endpoints in `/docs`.

### Planning and disruption handling
- Generate a proposed plan with explainable reasons, machine compatibility checks, capacity-based duration estimates and deadline warnings.
- Review/approve saved proposals; generated optimization proposals remain non-persistent and are not auto-approved.
- Run an OR-Tools CP-SAT scheduling model when OR-Tools is installed; otherwise the response explicitly identifies a deterministic heuristic fallback.
- Consider recorded product-specific wildcard machine speed factors in the core optimizer.
- Exclude machines with an active, recorded downtime event from the core optimizer.
- Simulate a temporary machine outage without changing saved plans.
- View schedule Gantt, machine lanes, decision-tree hierarchy and plan analytics.

### Multi-factory workflows and operating context
- Register multiple factories and link existing machines to a factory.
- Configure product-specific process sequences, required machine type, estimated process hours and worker count per step.
- Preview a sequential process workflow using compatible linked machines, available worker count (when workforce records exist), active downtime and verified historical speed factors.
- Record workers, skills, shift hours and availability.
- Save order-level worker demand profiles.
- Record machine performance factors with sample count and source notes.
- Record machine downtime events, including open outages.

### Closed-loop PoC and KPIs
- Record planned-vs-actual operation timestamps, quantities, downtime and delay reasons.
- Get feedback showing quantity/time variance and suggested follow-up; feedback does not silently modify future plans.
- Store a historical KPI baseline with period and source notes, and compare it with measurable current values.
- Record optimization request runtime and show it as a technical runtime KPI.
- The six-month roadmap card has been removed from the UI. Factory, workflow, workforce, downtime and closed-loop configuration modules remain available.

### Other modules
- Materials inventory and product BOM, with calculated material requirements and shortages.
- Optimization-grounded Production Advisor that compares three schedule strategies, ranks candidates using assignment and delivery-risk signals, and returns prioritized actions with evidence. It is a local decision-support engine, not a hosted generative LLM.
- Audit trail for important application actions.
- Interactive dashboard, system health and OpenAPI docs.

## Main API endpoints

- `GET /api/health`, `GET /api/dashboard`
- Orders: `GET/POST /api/orders`, `GET/PATCH/DELETE /api/orders/{order_id}`
- Import: `POST /api/import/preview`, `POST /api/import/confirm`
- Machines: `GET/POST /api/machines`, `PATCH/DELETE /api/machines/{code}`
- Plans: `POST /api/plans/generate`, `GET /api/plans`, `GET /api/plans/{plan_id}`, `POST /api/plans/{plan_id}/approve`
- Optimization: `POST /api/optimization/solve`; non-persistent proposed schedule
- Disruption: `POST /api/plans/simulate-disruption`
- Factory setup: `GET/POST /api/factories`, `POST /api/factories/{factory_code}/machines`
- Workflows: `GET/POST /api/workflows`, `DELETE /api/workflows/{step_id}`, `POST /api/workflows/preview`
- Workforce: `GET/POST /api/workers`, `POST /api/orders/{order_id}/planning-profile`
- Performance/downtime: `GET/POST /api/performance`, `GET/POST /api/machine-downtime`
- Execution and feedback: `GET/POST /api/operations/execution`, `GET /api/closed-loop/feedback`
- Demo data: `POST /api/demo/seed` (only succeeds when no orders or machines exist; creates clearly labeled synthetic records)
- Legacy roadmap API: `GET /api/poc/roadmap` (kept for compatibility; roadmap UI removed)
- KPI: `GET /api/kpis/current`, `POST /api/kpis/baseline`, `GET /api/kpis/compare`
- Materials/BOM: `GET/POST /api/materials`, `GET/POST /api/materials/bom`, `GET /api/materials/requirements`
- Audit and reports: `GET /api/audit`, `GET /api/reports/summary`, `GET /api/reports/orders.csv`

See `/docs` for request schemas and examples.

## Excel/CSV import format

Required columns: `order_id`, `product`, `quantity`, `due_date`. Optional columns include `priority` (1–5), `machine_type`, `customer`, `estimated_hours`, and `notes`. Common Arabic and English aliases are supported. XLSX and CSV are recommended; XLS depends on the installed reader engine. File size is limited by `MAX_UPLOAD_MB`.

Sample workbook: `sample_data/sample_orders.xlsx`. It is synthetic example data, not IB-02 factory data.

## KPI definitions and evidence rules

- **On-time delivery rate:** current value is only a proxy from completed orders and recorded production dates. True delivery KPI requires shipment timestamps.
- **Machine utilization and idle time:** current figures are estimates based on recorded execution runtimes and configured machine shift hours over a five-day reference week. They are not a full machine-state timeline.
- **Planning time:** average measured runtime of optimization requests, not the total time a planner spends preparing a plan manually.
- **Production lead time:** based on available actual production dates and may not equal order-receipt-to-completion lead time.
- **Schedule adherence:** uses execution records with both planned and actual start/end timestamps and a one-hour tolerance.
- **Baseline values:** manually entered and must be supported by factory evidence. Empty/unmeasurable metrics should not be represented as zero improvement.

Targets sometimes discussed in a proposal (for example, 70% lower planning time or 10% higher utilization) are hypotheses until measured. Do not claim them as achieved results before the PoC evaluation.

## Important limitations before real factory use

- No authentication, role-based permissions, TLS, tamper-evident audit, or multi-user access control. Keep the app on a trusted local machine; do not expose it to the public internet.
- Core order-level optimizer schedules each order as a single operation. The factory-specific multi-step workflow feature is currently a **sequential preview**, not yet a fully integrated multi-operation optimizer.
- Worker profiles and order labor demand are stored, but workforce skill/shift allocation is not yet a hard constraint in the core CP-SAT model. Workflow preview only checks total marked-available headcount when worker records exist.
- Shift calendars, weekends, holidays, setup/changeover matrices, material availability as a hard scheduling constraint, partial shifts, shipment events, and machine-specific calendars are not fully modeled.
- Machine outage what-if is simplified; recorded active downtime excludes that machine from optimization, but future timed outages are not yet modeled as complete calendar intervals in the optimizer.
- Historical performance factors are used where applicable, but one-off measurements should be validated before being treated as reliable production rates.
- No automatic ML training, autonomous machine control, or globally optimal result is guaranteed. OR-Tools status is returned when available; heuristic fallback makes no optimality claim.
- Validate all proposed schedules with production managers before use. Run the PoC in shadow mode before relying on recommendations operationally.

## Testing

From the project folder after installing requirements:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The test suite uses an isolated temporary SQLite database. Expected coverage includes health, order validation, imports, machine setup, planning/approval, materials, optimization proposal safety, KPI baseline comparison, factory workflow preview, performance factors, workforce records, execution feedback and PoC roadmap.

## IB-02 requirements traceability

See `docs/IB02_REQUIREMENTS_TRACEABILITY.md` for a requirement-by-requirement mapping from the supplied brief to working features and remaining engineering gaps.
