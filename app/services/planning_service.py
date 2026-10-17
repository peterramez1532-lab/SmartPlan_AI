from datetime import date, timedelta
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Order, Machine, Plan, PlanItem

def generate_plan(db: Session):
    orders = list(db.scalars(select(Order).where(Order.status.in_(["Pending","Planned","Delayed"]))))
    machines = list(db.scalars(select(Machine).where(Machine.available.is_(True))))
    orders.sort(key=lambda o: (-o.priority, o.due_date, o.order_id))
    plan = Plan(status="Proposed", rules="Deterministic heuristic: priority descending, due date ascending, compatible available machine with earliest projected finish; estimated hours and shift-hours duration; approved schedules and configured maintenance windows are reserved. Calendar-day approximation; no shift/holiday calendar or global-optimality claim.")
    db.add(plan); db.flush()
    # Each machine gets a sequential next-free date; no overlapping assignments are made.
    next_free = {m.code: date.today() for m in machines}
    # Approved plan items reserve machine time for later proposals.
    approved_ids = list(db.scalars(select(Plan.id).where(Plan.status == "Approved")))
    if approved_ids:
        reserved = db.scalars(select(PlanItem).where(PlanItem.plan_id.in_(approved_ids), PlanItem.machine_code.is_not(None), PlanItem.finish_date.is_not(None)))
        for reserved_item in reserved:
            next_free[reserved_item.machine_code] = max(next_free.get(reserved_item.machine_code, date.today()), reserved_item.finish_date + timedelta(days=1))
    items=[]
    for order in orders:
        compatible = [m for m in machines if m.machine_type.lower() == order.machine_type.lower() or order.machine_type.lower() == "general"]
        if not compatible:
            items.append(PlanItem(plan_id=plan.id, order_id=order.order_id, reason="No available compatible machine found.", warning="Unresolved: machine unavailable or incompatible.", late=True))
            continue
        candidates=[]
        for m in compatible:
            start=max(date.today(),next_free[m.code])
            hours=order.estimated_hours if order.estimated_hours else order.quantity / m.capacity_per_day * m.shift_hours
            days=max(1, int((hours / m.shift_hours) + 0.999999))
            finish=start+timedelta(days=days-1)
            # Move the entire job past maintenance if its proposed date range overlaps it.
            if m.maintenance_start and m.maintenance_end and start <= m.maintenance_end and finish >= m.maintenance_start:
                start=m.maintenance_end+timedelta(days=1)
                finish=start+timedelta(days=days-1)
            candidates.append((finish,start,m,hours,days))
        finish,start,m,hours,days=min(candidates,key=lambda x:(x[0],x[1],x[2].code))
        next_free[m.code]=finish+timedelta(days=1)
        late=finish>order.due_date
        reason=f"Assigned to {m.code}: compatible type; earliest projected finish among available candidates. Sorted by priority ({order.priority}) then deadline; estimated {hours:.1f} machine-hours."
        warning="Projected finish is after delivery deadline." if late else None
        items.append(PlanItem(plan_id=plan.id,order_id=order.order_id,machine_code=m.code,start_date=start,finish_date=finish,estimated_hours=hours,late=late,reason=reason,warning=warning))
    db.add_all(items); db.commit()
    return plan, items

def approve_plan(db: Session, plan_id: int):
    plan=db.get(Plan,plan_id)
    if not plan: return None
    if plan.status != "Proposed": raise ValueError("Only proposed plans can be approved")
    plan.status="Approved"
    for item in db.scalars(select(PlanItem).where(PlanItem.plan_id==plan_id)):
        order=db.scalar(select(Order).where(Order.order_id==item.order_id))
        if order and order.status=="Pending": order.status="Approved"
    db.commit()
    return plan
