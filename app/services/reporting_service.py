from sqlalchemy import select, func
from app.models import Order, Machine, Plan, PlanItem, ActualProduction

def dashboard(db):
    count=lambda q: db.scalar(q) or 0
    return {
      "total_orders":count(select(func.count(Order.id))),
      "pending_orders":count(select(func.count(Order.id)).where(Order.status=="Pending")),
      "completed_orders":count(select(func.count(Order.id)).where(Order.status=="Completed")),
      "delayed_orders":count(select(func.count(Order.id)).where(Order.status=="Delayed")),
      "available_machines":count(select(func.count(Machine.id)).where(Machine.available.is_(True))),
      "proposed_plans":count(select(func.count(Plan.id)).where(Plan.status=="Proposed")),
      "approved_plans":count(select(func.count(Plan.id)).where(Plan.status=="Approved")),
      "planned_quantity":count(select(func.sum(Order.quantity))),
      "actual_quantity":count(select(func.sum(ActualProduction.actual_quantity)))
    }
