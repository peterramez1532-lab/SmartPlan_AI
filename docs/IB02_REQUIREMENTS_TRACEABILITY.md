# IB-02 brief → SmartPlan AI traceability

This document maps the uploaded six-page IB-02 brief to the current implementation. It distinguishes implemented capabilities from the remaining work needed for a real factory PoC.

| Brief requirement | Current implementation | Status / limitation |
|---|---|---|
| Replace spreadsheet-based order intake | Order CRUD, Excel/CSV preview/import, duplicate and row validation | Implemented for the MVP; must map actual source columns with IB-02 |
| Many orders, machines and deadlines | Orders, machines, priorities, due dates, capacities, estimated hours | Implemented as configurable records; sample data is synthetic |
| Assign an order to a compatible machine and estimate schedule | CP-SAT proposal if installed, otherwise deterministic heuristic | Implemented as single-operation-per-order; calendar model is simplified |
| Workforce availability and labor demand | Worker records, skills/availability/shift hours, order labor profile, per-step worker count | Stored; total available headcount checked in workflow preview; skill/shift allocation is not yet enforced in the core optimizer |
| Different factory/product workflows | Factory registry, factory-machine links, ordered product process steps | Implemented as configuration and sequential workflow preview; not yet integrated into one multi-operation CP-SAT model |
| Historical machine performance | Performance factor records with sample count/source note; factor used in compatible scheduling and workflow previews | Implemented as a simple multiplicative factor; must be estimated from verified samples |
| Current production and actual feedback | Actual production records and operation execution with planned/actual timestamps, output and downtime | Implemented; factory integration and data validation are still required |
| Machine breakdown and rescheduling | What-if outage simulation and recorded downtime events; active downtime machines are excluded from optimizer | Implemented in simplified form; future outage intervals and automatic live rescheduling are not complete |
| Recommended schedule with human review | Plan proposals, plan detail, Gantt/decision-tree/analytics and explicit approval endpoint | Implemented; proposal must not be treated as a machine instruction |
| Closed-loop: operations → feedback → better next schedule | Closed-loop feedback endpoint shows duration/quantity variances and suggested follow-up | Feedback is advisory; no automatic self-learning or silent schedule change |
| KPIs | Baseline register and current/compare APIs for on-time delivery, utilization, planning runtime, idle estimate, lead time, schedule adherence | Some are proxies/approximations. Use verified, agreed definitions and data before reporting results |
| PoC on real factory data | KPI baseline register, source notes, proposal-only workflow and requirements traceability | Roadmap UI intentionally removed; no real IB-02 dataset was supplied with this project |
| PoC budget ceiling up to USD 30,000 | The source brief records the ceiling; the roadmap card was removed from the user interface | Not a detailed budget or cost quote; cost breakdown must be built after discovery |
| Scale from PoC to pilot and commercial deployment | Roadmap includes shadow mode and scale decision | Enterprise security, role-based access, full factory integration and production-grade optimizer remain future work |

## Source brief: six-month PoC outline (documentation only; not shown in the application UI)

1. **Month 1 — Discovery & data audit:** map orders, machines, workers, rates, deadlines, history and current manual-planning effort; choose one line and agree KPI definitions.
2. **Month 2 — Integration & workflow model:** import and validate real data, configure factory-specific routes and compatibility, document data gaps.
3. **Month 3 — Scheduling engine:** test constrained proposals, runtime, late/unassigned jobs and disruption scenarios.
4. **Month 4 — Closed-loop operations:** record planned-vs-actual execution, downtime and verified performance feedback.
5. **Month 5 — Shadow-mode PoC:** run beside the existing manual process without controlling machines; compare equivalent order sets and constraints.
6. **Month 6 — Evaluation & scale decision:** report measured KPIs, feasibility, data quality, limitations, risks and recommendation for a larger pilot.

## Evaluation rules

- Capture baseline before comparing outcomes.
- Compare manual and proposed schedules on comparable order sets and the same hard constraints.
- Report solver name/status, runtime, assigned/unassigned orders, deadline risk, and any human overrides.
- Do not claim a KPI improvement unless it is calculated from source data with a documented period and definition.
- Keep a production manager responsible for approval; the PoC does not autonomously control machines.
