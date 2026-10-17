"""Constrained scheduling adapter. Uses OR-Tools CP-SAT when installed; otherwise an explicit heuristic fallback."""
from datetime import date, datetime, timedelta
from sqlalchemy import select
from app.models import Order, Machine, MachinePerformanceRecord, MachineDowntimeEvent


def solve_schedule(db, workers_available=None, outage_machine=None, outage_hours=0, max_time_seconds=5, planning_goal="balanced"):
    orders = list(db.scalars(select(Order).where(Order.status.in_(["Pending", "Planned", "Delayed"]))))
    machines = list(db.scalars(select(Machine).where(Machine.available.is_(True))))
    now = datetime.now()
    down_codes = {event.machine_code for event in db.scalars(select(MachineDowntimeEvent))
                  if event.start_at <= now and (event.end_at is None or event.end_at > now)}
    machines = [machine for machine in machines if machine.code not in down_codes]
    if planning_goal not in {"balanced", "deadline_first", "throughput"}:
        planning_goal = "balanced"
    if planning_goal == "deadline_first":
        orders.sort(key=lambda o: (o.due_date, -o.priority, o.order_id))
    elif planning_goal == "throughput":
        orders.sort(key=lambda o: (float(o.estimated_hours or o.quantity), o.due_date, -o.priority, o.order_id))
    else:
        orders.sort(key=lambda o: (-o.priority, o.due_date, o.order_id))
    performance_factors = {}
    for order in orders:
        for machine in machines:
            record = db.scalar(select(MachinePerformanceRecord).where(
                MachinePerformanceRecord.machine_code == machine.code,
                MachinePerformanceRecord.product.in_([order.product, "*"]),
                MachinePerformanceRecord.process_code == "*"
            ).order_by(MachinePerformanceRecord.recorded_at.desc()))
            performance_factors[(order.order_id, machine.code)] = record.speed_factor if record else 1.0
    if not orders:
        return {"solver":"OR-Tools CP-SAT" if _ortools_available() else "deterministic heuristic fallback", "optimality":"No open orders", "items":[], "summary":{"orders":0,"late":0,"unassigned":0}}
    if workers_available is not None and (not isinstance(workers_available, int) or workers_available < 0):
        raise ValueError("workers_available must be a non-negative integer")
    try:
        from ortools.sat.python import cp_model
        result = _solve_cp_sat(orders, machines, cp_model, workers_available, outage_machine, outage_hours, max_time_seconds, performance_factors, planning_goal)
        if result is not None:
            return result
    except ImportError:
        pass
    result = _solve_heuristic(orders, machines, workers_available, outage_machine, outage_hours, performance_factors, planning_goal)
    result["planning_goal"] = planning_goal
    return result


def _ortools_available():
    try:
        import ortools  # noqa
        return True
    except ImportError:
        return False


def _duration(order, machine, factor=1.0):
    hours = float(order.estimated_hours) if order.estimated_hours else float(order.quantity) / max(float(machine.capacity_per_day), .001) * float(machine.shift_hours)
    return max(1, int(hours * float(factor) + .999999))


def _solve_cp_sat(orders, machines, cp_model, workers_available, outage_machine, outage_hours, max_time_seconds, performance_factors, planning_goal="balanced"):
    if not machines:
        return _result([], len(orders), "OR-Tools CP-SAT", "No available machines; all orders unresolved")
    model = cp_model.CpModel()
    # Discrete hourly horizon from today. Bound horizon to 90 days for predictable PoC performance.
    horizon = 24 * 90
    starts, ends, presences, intervals = {}, {}, {}, {m.code: [] for m in machines}
    assigned = {}
    today = date.today()
    for oi, order in enumerate(orders):
        choices=[]
        for mi, machine in enumerate(machines):
            if order.machine_type.lower() not in ("general", machine.machine_type.lower()):
                continue
            dur = _duration(order, machine, performance_factors.get((order.order_id, machine.code), 1.0))
            start = model.NewIntVar(0, horizon, f"s_{oi}_{mi}")
            end = model.NewIntVar(0, horizon, f"e_{oi}_{mi}")
            present = model.NewBoolVar(f"p_{oi}_{mi}")
            interval = model.NewOptionalIntervalVar(start, dur, end, present, f"i_{oi}_{mi}")
            starts[(oi,mi)], ends[(oi,mi)], presences[(oi,mi)] = start,end,present
            intervals[machine.code].append(interval)
            model.Add(end == start + dur).OnlyEnforceIf(present)
            # Machine outage represented as a reserved interval, in hours from now.
            if outage_machine == machine.code and outage_hours > 0:
                # Optional intervals cannot overlap a fixed outage interval when present.
                outage_start = 0
                outage_end = min(horizon, int(outage_hours))
                model.Add(end <= outage_start).OnlyEnforceIf(present) if False else None
                model.Add(start >= outage_end).OnlyEnforceIf(present)
            choices.append(present)
        if choices:
            model.Add(sum(choices) <= 1)
            assigned[oi] = choices
    for machine in machines:
        model.AddNoOverlap(intervals[machine.code])
    # Objective: prioritize assignment, then deadline lateness and earlier completion. All weights are explicit.
    terms=[]
    for oi, order in enumerate(orders):
        choices=assigned.get(oi, [])
        if not choices:
            continue
        terms.append(100000 * (1 - sum(choices)))
        due_hour=max(0, min(horizon, (order.due_date - today).days * 24 + 23))
        for mi, machine in enumerate(machines):
            if (oi,mi) not in starts: continue
            present=presences[(oi,mi)]; end=ends[(oi,mi)]
            late=model.NewIntVar(0,horizon,f"late_{oi}_{mi}")
            model.Add(late >= end - due_hour).OnlyEnforceIf(present)
            model.Add(late == 0).OnlyEnforceIf(present.Not())
            if planning_goal == "deadline_first":
                late_weight = 500 + order.priority * 50
                completion_weight = 1
            elif planning_goal == "throughput":
                late_weight = 100 + order.priority * 10
                completion_weight = 1
            else:
                late_weight = 250 + order.priority * 25
                completion_weight = 2
            terms.append(late * late_weight)
            terms.append(end * completion_weight)
    model.Minimize(sum(terms))
    solver=cp_model.CpSolver(); solver.parameters.max_time_in_seconds=max(1,min(30,float(max_time_seconds)))
    status=solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE): return None
    rows=[]
    for oi, order in enumerate(orders):
        chosen=None
        for mi,machine in enumerate(machines):
            if (oi,mi) in presences and solver.Value(presences[(oi,mi)]):
                chosen=(mi,machine); break
        if not chosen:
            rows.append(_unassigned(order.order_id,"No compatible assignment found inside the 90-day optimization horizon.")); continue
        mi,machine=chosen; start_h=solver.Value(starts[(oi,mi)]); end_h=solver.Value(ends[(oi,mi)])
        start=today+timedelta(days=start_h//24); finish=today+timedelta(days=max(0,end_h-1)//24)
        late=finish>order.due_date
        rows.append({"order_id":order.order_id,"machine_code":machine.code,"start_date":start.isoformat(),"finish_date":finish.isoformat(),"start_hour":start_h%24,"finish_hour":end_h%24,"estimated_hours":_duration(order,machine),"late":late,"warning":"Projected finish after deadline" if late else None,"reason":f"CP-SAT assignment: compatible machine {machine.code}; no overlapping machine intervals; objective prioritizes assignment and weighted deadline adherence."})
    result = _result(rows,len(orders),"OR-Tools CP-SAT", "Optimal solution found" if status==cp_model.OPTIMAL else "Feasible solution found; optimality not proven")
    result["planning_goal"] = planning_goal
    return result


def _solve_heuristic(orders,machines,workers_available,outage_machine,outage_hours,performance_factors,planning_goal="balanced"):
    free={m.code: int(outage_hours) if m.code==outage_machine else 0 for m in machines}
    rows=[]; workers_note=f" Workforce availability input={workers_available}; detailed worker skills/shift constraints require configured workforce records." if workers_available is not None else " Workforce capacity not constrained because no workforce availability was supplied."
    for order in orders:
        candidates=[]
        for m in machines:
            if order.machine_type.lower() not in ("general",m.machine_type.lower()): continue
            dur=_duration(order,m,performance_factors.get((order.order_id,m.code),1.0)); start=free[m.code]; finish=start+dur
            candidates.append((finish,start,m,dur))
        if not candidates:
            rows.append(_unassigned(order.order_id,"No available compatible machine.")); continue
        finish,start,m,dur=min(candidates,key=lambda x:(x[0],x[1],x[2].code))
        free[m.code]=finish
        start_date=date.today()+timedelta(hours=start)
        finish_date=date.today()+timedelta(hours=max(start,finish-1))
        late=finish_date>order.due_date
        rows.append({"order_id":order.order_id,"machine_code":m.code,"start_date":start_date.isoformat(),"finish_date":finish_date.isoformat(),"start_hour":start%24,"finish_hour":finish%24,"estimated_hours":dur,"late":late,"warning":"Projected finish after deadline" if late else None,"reason":f"Deterministic earliest-finish heuristic; compatible machine; no machine overlap.{workers_note}"})
    return _result(rows,len(orders),"deterministic heuristic fallback","Heuristic result; no global optimality claim")


def _unassigned(order_id,reason):
    return {"order_id":order_id,"machine_code":None,"start_date":None,"finish_date":None,"start_hour":None,"finish_hour":None,"estimated_hours":None,"late":True,"warning":reason,"reason":reason}


def _result(rows,total,solver,optimality):
    return {"solver":solver,"optimality":optimality,"persisted":False,"items":rows,"summary":{"orders":total,"assigned":sum(bool(x["machine_code"]) for x in rows),"late":sum(bool(x["late"]) for x in rows),"unassigned":sum(not x["machine_code"] for x in rows)}}
