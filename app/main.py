import csv, io, logging, time, json
from datetime import date, datetime, timedelta
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError
from app.config import APP_NAME, MAX_UPLOAD_MB, ROOT
from app.database import get_db, init_db
from app.models import Order, Machine, Plan, PlanItem, ActualProduction, Material, ProductMaterial, AuditLog, KPIBaseline, Factory, FactoryMachineLink, ProductProcessStep, Worker, OrderPlanningProfile, MachinePerformanceRecord, MachineDowntimeEvent, OperationExecution, PlanningRun
from app.schemas.schemas import OrderIn, OrderUpdate, MachineIn, ActualIn, MaterialIn, MaterialUpdate, BOMIn
from app.services.import_service import parse_file, commit_import
from app.services.planning_service import generate_plan, approve_plan
from app.services.optimization_service import solve_schedule
from app.services.reporting_service import dashboard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log=logging.getLogger("smartplan")
app=FastAPI(title=APP_NAME, description="Explainable production planning MVP. Plans are proposals until a manager approves them.", version="1.1.0")
app.mount("/static", StaticFiles(directory=str(ROOT/"app"/"static")), name="static")

def audit(db: Session, action: str, entity_type: str, entity_id: str, details: str = ""):
    db.add(AuditLog(action=action, entity_type=entity_type, entity_id=str(entity_id), details=details))

@app.on_event("startup")
def startup(): init_db()

@app.get("/", response_class=HTMLResponse)
def home():
    return (ROOT/"app"/"templates"/"index.html").read_text(encoding="utf-8")

@app.get("/api/health")
def health(db:Session=Depends(get_db)):
    try: db.execute(select(1))
    except Exception as e: raise HTTPException(503,"Database unavailable") from e
    return {"status":"ok","database":"connected","database_type":"sqlite" if "sqlite" in str(db.bind.url) else "postgresql"}

@app.post("/api/demo/seed", status_code=201)
def seed_demo_data(db: Session = Depends(get_db)):
    """Seed synthetic data only into an empty production-order/machine workspace."""
    if db.scalar(select(Order.id).limit(1)) or db.scalar(select(Machine.id).limit(1)):
        raise HTTPException(409, "Demo data was not loaded: this workspace already contains orders or machines. Use a separate empty database for the demo.")
    today = date.today()
    machine_rows = [
        Machine(code="DEMO-M1", name="Mesh Line Alpha", machine_type="mesh", capacity_per_day=520, shift_hours=8, available=True, notes="Synthetic demo data"),
        Machine(code="DEMO-M2", name="Mesh Line Beta", machine_type="mesh", capacity_per_day=430, shift_hours=8, available=True, notes="Synthetic demo data"),
        Machine(code="DEMO-M3", name="Precision Mesh Line", machine_type="mesh", capacity_per_day=610, shift_hours=8, available=True, notes="Synthetic demo data"),
        Machine(code="DEMO-M4", name="Flexible Backup Line", machine_type="mesh", capacity_per_day=350, shift_hours=8, available=True, notes="Synthetic demo data"),
    ]
    order_specs = [
        ("DEMO-1001", "Galvanized wire mesh", 1400, 5, 2, "High priority customer order"),
        ("DEMO-1002", "Welded mesh panel", 900, 4, 3, "Standard production order"),
        ("DEMO-1003", "Fine mesh roll", 600, 5, 1, "Tight delivery window"),
        ("DEMO-1004", "Industrial mesh sheet", 1800, 3, 5, "Large batch"),
        ("DEMO-1005", "Fence mesh panel", 750, 4, 4, "Customer delivery commitment"),
        ("DEMO-1006", "Galvanized wire mesh", 1100, 2, 2, "Normal priority"),
        ("DEMO-1007", "Welded mesh panel", 420, 5, 1, "Short urgent run"),
        ("DEMO-1008", "Industrial mesh sheet", 1250, 3, 6, "Flexible delivery date"),
    ]
    for machine in machine_rows:
        db.add(machine)
    for oid, product, qty, priority, due_in_days, notes in order_specs:
        db.add(Order(order_id=oid, product=product, quantity=qty, order_date=today, due_date=today + timedelta(days=due_in_days), priority=priority, machine_type="mesh", estimated_hours=round(qty / 180, 1), customer="Demo Customer", notes=f"SYNTHETIC DEMO ONLY — {notes}", status="Pending"))
    db.add_all([
        MachinePerformanceRecord(machine_code="DEMO-M1", product="*", process_code="*", speed_factor=1.0, sample_count=12, source_note="Synthetic demonstration factor; replace with verified factory history"),
        MachinePerformanceRecord(machine_code="DEMO-M2", product="*", process_code="*", speed_factor=1.15, sample_count=8, source_note="Synthetic demonstration factor; not real IB-02 data"),
    ])
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        log.exception("Could not seed demo data")
        raise HTTPException(500, "Demo data could not be created; no existing records were intentionally modified.") from exc
    audit(db, "seed_demo", "workspace", "synthetic-demo", "Created 8 synthetic orders, 4 synthetic machines and 2 illustrative performance records")
    db.commit()
    return {"status": "created", "orders_created": len(order_specs), "machines_created": len(machine_rows), "performance_records_created": 2, "synthetic": True, "warning": "Synthetic data only; not real IB-02 factory data."}


@app.get("/api/dashboard")
def get_dashboard(db:Session=Depends(get_db)): return dashboard(db)

def order_out(o):
    return {k:getattr(o,k) for k in ["id","order_id","product","quantity","order_date","due_date","priority","machine_type","estimated_hours","customer","notes","status"]}

@app.get("/api/orders")
def list_orders(search:str|None=None,status:str|None=None,sort_by:str="due_date",direction:str="asc",limit:int=100,offset:int=0,db:Session=Depends(get_db)):
    allowed={"due_date","priority","order_id","quantity","status","product"}
    if sort_by not in allowed: raise HTTPException(400,"Unsupported sort field")
    if direction not in {"asc","desc"}: raise HTTPException(400,"direction must be asc or desc")
    q=select(Order)
    if search: q=q.where(or_(Order.order_id.contains(search),Order.product.contains(search),Order.customer.contains(search)))
    if status: q=q.where(Order.status==status)
    col=getattr(Order,sort_by); q=q.order_by(col.desc() if direction=="desc" else col.asc()).offset(max(0,offset)).limit(min(max(1,limit),500))
    return [order_out(o) for o in db.scalars(q)]

@app.post("/api/orders",status_code=201)
def create_order(payload:OrderIn,db:Session=Depends(get_db)):
    obj=Order(**payload.model_dump()); db.add(obj)
    try: db.commit(); db.refresh(obj)
    except IntegrityError: db.rollback(); raise HTTPException(409,"order_id already exists")
    audit(db, "create", "order", obj.order_id, f"status={obj.status}; quantity={obj.quantity}"); db.commit()
    return order_out(obj)

@app.get("/api/orders/{order_id}")
def get_order(order_id:str,db:Session=Depends(get_db)):
    o=db.scalar(select(Order).where(Order.order_id==order_id))
    if not o: raise HTTPException(404,"Order not found")
    return order_out(o)

@app.patch("/api/orders/{order_id}")
def update_order(order_id:str,payload:OrderUpdate,db:Session=Depends(get_db)):
    o=db.scalar(select(Order).where(Order.order_id==order_id))
    if not o: raise HTTPException(404,"Order not found")
    for k,v in payload.model_dump(exclude_unset=True).items():
        if v is not None: setattr(o,k,v)
    db.commit(); audit(db, "update", "order", o.order_id, "Order fields updated"); db.commit(); db.refresh(o); return order_out(o)

@app.delete("/api/orders/{order_id}",status_code=204)
def delete_order(order_id:str,db:Session=Depends(get_db)):
    o=db.scalar(select(Order).where(Order.order_id==order_id))
    if not o: raise HTTPException(404,"Order not found")
    audit(db, "delete", "order", o.order_id, "Order deleted"); db.delete(o); db.commit()

@app.get("/api/machines")
def list_machines(db:Session=Depends(get_db)):
    return [{k:getattr(m,k) for k in ["id","code","name","machine_type","capacity_per_day","available","shift_hours","maintenance_start","maintenance_end","notes"]} for m in db.scalars(select(Machine).order_by(Machine.code))]

@app.post("/api/machines",status_code=201)
def create_machine(payload:MachineIn,db:Session=Depends(get_db)):
    if payload.maintenance_start and payload.maintenance_end and payload.maintenance_end<payload.maintenance_start: raise HTTPException(422,"maintenance_end must be on or after maintenance_start")
    m=Machine(**payload.model_dump()); db.add(m)
    try: db.commit(); db.refresh(m)
    except IntegrityError: db.rollback(); raise HTTPException(409,"Machine code already exists")
    audit(db, "create", "machine", m.code, f"type={m.machine_type}; capacity_per_day={m.capacity_per_day}"); db.commit()
    return {"id":m.id,**payload.model_dump()}

@app.patch("/api/machines/{code}")
def update_machine(code:str,payload:MachineIn,db:Session=Depends(get_db)):
    m=db.scalar(select(Machine).where(Machine.code==code))
    if not m: raise HTTPException(404,"Machine not found")
    for k,v in payload.model_dump().items(): setattr(m,k,v)
    audit(db, "update", "machine", m.code, "Machine fields updated"); db.commit(); db.refresh(m); return {"id":m.id,**payload.model_dump()}

@app.delete("/api/machines/{code}",status_code=204)
def delete_machine(code:str,db:Session=Depends(get_db)):
    m=db.scalar(select(Machine).where(Machine.code==code))
    if not m: raise HTTPException(404,"Machine not found")
    audit(db, "delete", "machine", m.code, "Machine deleted"); db.delete(m); db.commit()

@app.post("/api/import/preview")
async def import_preview(file:UploadFile=File(...)):
    if not file.filename: raise HTTPException(400,"Filename required")
    if not file.filename.lower().endswith((".xlsx",".xls",".csv")): raise HTTPException(415,"Only XLSX, XLS, and CSV files are supported")
    data=await file.read(MAX_UPLOAD_MB*1024*1024+1)
    if len(data)>MAX_UPLOAD_MB*1024*1024: raise HTTPException(413,f"File exceeds {MAX_UPLOAD_MB} MB limit")
    try: return parse_file(data,file.filename)
    except ValueError as e: raise HTTPException(422,str(e))

@app.post("/api/import/confirm")
async def import_confirm(file:UploadFile=File(...),db:Session=Depends(get_db)):
    data=await file.read(MAX_UPLOAD_MB*1024*1024+1)
    if len(data)>MAX_UPLOAD_MB*1024*1024: raise HTTPException(413,"File too large")
    try: parsed=parse_file(data,file.filename or "")
    except ValueError as e: raise HTTPException(422,str(e))
    if parsed["missing_columns"]: raise HTTPException(422,{"message":"Required columns missing","details":parsed["missing_columns"]})
    try:
        result = commit_import(db,parsed)
        audit(db, "import", "orders", "batch", f"inserted={result['inserted']}; rejected={result['rejected']}; skipped={result['skipped']}")
        db.commit()
        return result
    except Exception as e: log.exception("Import failed"); raise HTTPException(500,"Import transaction failed; no rows were committed") from e

@app.post("/api/plans/generate",status_code=201)
def plan_generate(db:Session=Depends(get_db)):
    plan,items=generate_plan(db)
    audit(db, "generate", "plan", plan.id, f"status={plan.status}; items={len(items)}"); db.commit()
    return {"id":plan.id,"status":plan.status,"created_at":plan.created_at,"rules":plan.rules,"items":[{k:getattr(i,k) for k in ["order_id","machine_code","start_date","finish_date","estimated_hours","late","reason","warning"]} for i in items]}


@app.post("/api/plans/simulate-disruption")
def simulate_disruption(payload: dict, db: Session = Depends(get_db)):
    """Run a non-persistent what-if reschedule after a temporary machine outage."""
    machine_code = str(payload.get("machine_code", "")).strip()
    try:
        outage_days = int(payload.get("outage_days", 1))
    except (TypeError, ValueError):
        raise HTTPException(422, "outage_days must be a whole number")
    if not machine_code:
        raise HTTPException(422, "machine_code is required")
    if outage_days < 1 or outage_days > 90:
        raise HTTPException(422, "outage_days must be between 1 and 90")
    machines = list(db.scalars(select(Machine).where(Machine.available.is_(True))))
    down = next((m for m in machines if m.code.lower() == machine_code.lower()), None)
    if not down:
        raise HTTPException(404, "Available machine not found")
    orders = list(db.scalars(select(Order).where(Order.status.in_(["Pending", "Planned", "Delayed"]))))
    orders.sort(key=lambda o: (-o.priority, o.due_date, o.order_id))
    today = date.today()
    free = {m.code: today + ( __import__('datetime').timedelta(days=outage_days) if m.code == down.code else __import__('datetime').timedelta(0)) for m in machines}
    rows=[]
    for order in orders:
        compatible=[m for m in machines if m.machine_type.lower()==order.machine_type.lower() or order.machine_type.lower()=="general"]
        choices=[]
        for m in compatible:
            start=free[m.code]
            hours=float(order.estimated_hours) if order.estimated_hours else float(order.quantity)/float(m.capacity_per_day)*float(m.shift_hours)
            days=max(1, int((hours/float(m.shift_hours))+0.999999))
            finish=start+__import__('datetime').timedelta(days=days-1)
            if m.maintenance_start and m.maintenance_end and start <= m.maintenance_end and finish >= m.maintenance_start:
                start=m.maintenance_end+__import__('datetime').timedelta(days=1); finish=start+__import__('datetime').timedelta(days=days-1)
            choices.append((finish,start,m,hours,days))
        if not choices:
            rows.append({"order_id":order.order_id,"machine_code":None,"start_date":None,"finish_date":None,"estimated_hours":None,"late":True,"warning":"No compatible available machine in scenario","reason":"Unresolved after disruption"})
            continue
        finish,start,m,hours,days=min(choices,key=lambda x:(x[0],x[1],x[2].code))
        free[m.code]=finish+__import__('datetime').timedelta(days=1)
        late=finish>order.due_date
        rows.append({"order_id":order.order_id,"machine_code":m.code,"start_date":start.isoformat(),"finish_date":finish.isoformat(),"estimated_hours":round(hours,2),"late":late,"warning":"Projected finish after deadline" if late else None,"reason":f"What-if scenario: {down.code} unavailable for {outage_days} day(s); earliest-finish compatible assignment."})
    return {"scenario":"machine_disruption","machine_code":down.code,"outage_days":outage_days,"persisted":False,"rules":"Temporary outage window applied to the selected machine; deterministic earliest-finish heuristic; calendar-day approximation; no global-optimality claim.","items":rows,"summary":{"orders":len(rows),"late_or_unresolved":sum(1 for r in rows if r["late"]),"reassigned_to_other_machine":sum(1 for r in rows if r["machine_code"] and r["machine_code"] != down.code),"unresolved":sum(1 for r in rows if not r["machine_code"])}}

@app.get("/api/plans")
def plans(db:Session=Depends(get_db)):
    return [{"id":p.id,"status":p.status,"created_at":p.created_at,"rules":p.rules} for p in db.scalars(select(Plan).order_by(Plan.id.desc()))]

@app.get("/api/plans/{plan_id}")
def plan_detail(plan_id:int,db:Session=Depends(get_db)):
    p=db.get(Plan,plan_id)
    if not p: raise HTTPException(404,"Plan not found")
    items=db.scalars(select(PlanItem).where(PlanItem.plan_id==plan_id))
    return {"id":p.id,"status":p.status,"created_at":p.created_at,"rules":p.rules,"items":[{k:getattr(i,k) for k in ["order_id","machine_code","start_date","finish_date","estimated_hours","late","reason","warning"]} for i in items]}

@app.post("/api/plans/{plan_id}/approve")
def plan_approve(plan_id:int,db:Session=Depends(get_db)):
    try: p=approve_plan(db,plan_id)
    except ValueError as e: raise HTTPException(409,str(e))
    if not p: raise HTTPException(404,"Plan not found")
    return {"id":p.id,"status":p.status}

@app.post("/api/actual")
def actual_create(payload:ActualIn,db:Session=Depends(get_db)):
    o=db.scalar(select(Order).where(Order.order_id==payload.order_id))
    if not o: raise HTTPException(404,"Order not found")
    m=db.scalar(select(Machine).where(Machine.code==payload.machine_code))
    if not m: raise HTTPException(404,"Machine not found")
    a=ActualProduction(**payload.model_dump()); db.add(a)
    if payload.actual_quantity>=o.quantity: o.status="Completed"
    elif payload.production_date>o.due_date: o.status="Delayed"
    elif o.status in ("Pending","Approved","Planned"): o.status="In Production"
    db.commit(); audit(db, "record", "actual_production", a.id, f"order={a.order_id}; actual={a.actual_quantity}"); db.commit(); db.refresh(a)
    return {"id":a.id,**payload.model_dump(),"completion_percent":round(payload.actual_quantity/o.quantity*100,1)}

@app.get("/api/actual")
def actual_list(db:Session=Depends(get_db)):
    return [{"id":a.id,**{k:getattr(a,k) for k in ["order_id","production_date","machine_code","planned_quantity","actual_quantity","downtime_hours","delay_reason","notes"]}} for a in db.scalars(select(ActualProduction).order_by(ActualProduction.production_date.desc()))]

@app.get("/api/materials")
def list_materials(db: Session = Depends(get_db)):
    rows = db.scalars(select(Material).order_by(Material.code))
    return [{"id":m.id,"code":m.code,"name":m.name,"unit":m.unit,"on_hand":m.on_hand,"reorder_point":m.reorder_point,"supplier":m.supplier,"notes":m.notes,"low_stock":m.on_hand <= m.reorder_point} for m in rows]

@app.post("/api/materials", status_code=201)
def create_material(payload: MaterialIn, db: Session = Depends(get_db)):
    m = Material(**payload.model_dump()); db.add(m)
    try: db.commit(); db.refresh(m)
    except IntegrityError: db.rollback(); raise HTTPException(409, "Material code already exists")
    audit(db, "create", "material", m.code, f"on_hand={m.on_hand} {m.unit}"); db.commit()
    return {"id":m.id, **payload.model_dump(), "low_stock":m.on_hand <= m.reorder_point}

@app.patch("/api/materials/{code}")
def update_material(code: str, payload: MaterialUpdate, db: Session = Depends(get_db)):
    m = db.scalar(select(Material).where(Material.code == code))
    if not m: raise HTTPException(404, "Material not found")
    changes = payload.model_dump(exclude_unset=True)
    for k,v in changes.items(): setattr(m,k,v)
    audit(db, "update", "material", code, ", ".join(changes.keys()) or "No fields changed"); db.commit(); db.refresh(m)
    return {"id":m.id,"code":m.code,"name":m.name,"unit":m.unit,"on_hand":m.on_hand,"reorder_point":m.reorder_point,"supplier":m.supplier,"notes":m.notes,"low_stock":m.on_hand <= m.reorder_point}

@app.delete("/api/materials/{code}", status_code=204)
def delete_material(code: str, db: Session = Depends(get_db)):
    m = db.scalar(select(Material).where(Material.code == code))
    if not m: raise HTTPException(404, "Material not found")
    if db.scalar(select(ProductMaterial.id).where(ProductMaterial.material_id == m.id)) is not None: raise HTTPException(409, "Material is used in a product bill of materials; remove those links first")
    audit(db, "delete", "material", code, "Material deleted"); db.delete(m); db.commit()

@app.get("/api/materials/bom")
def list_bom(db: Session = Depends(get_db)):
    rows = db.execute(select(ProductMaterial, Material).join(Material, ProductMaterial.material_id == Material.id)).all()
    return [{"id":b.id,"product":b.product,"material_code":m.code,"material_name":m.name,"unit":m.unit,"quantity_per_unit":b.quantity_per_unit,"available_stock":m.on_hand} for b,m in rows]

@app.post("/api/materials/bom", status_code=201)
def create_bom(payload: BOMIn, db: Session = Depends(get_db)):
    m = db.scalar(select(Material).where(Material.code == payload.material_code))
    if not m: raise HTTPException(404, "Material code not found")
    existing = db.scalar(select(ProductMaterial).where(ProductMaterial.product == payload.product, ProductMaterial.material_id == m.id))
    if existing: raise HTTPException(409, "This product/material requirement already exists")
    b = ProductMaterial(product=payload.product, material_id=m.id, quantity_per_unit=payload.quantity_per_unit); db.add(b); db.commit(); db.refresh(b)
    audit(db, "create", "bill_of_materials", b.id, f"product={b.product}; material={m.code}"); db.commit()
    return {"id":b.id,"product":b.product,"material_code":m.code,"quantity_per_unit":b.quantity_per_unit}

@app.delete("/api/materials/bom/{bom_id}", status_code=204)
def delete_bom(bom_id: int, db: Session = Depends(get_db)):
    b = db.get(ProductMaterial, bom_id)
    if not b: raise HTTPException(404, "Bill-of-material row not found")
    audit(db, "delete", "bill_of_materials", bom_id, f"product={b.product}"); db.delete(b); db.commit()

@app.get("/api/materials/requirements")
def material_requirements(db: Session = Depends(get_db)):
    active = list(db.scalars(select(Order).where(Order.status.in_(["Pending", "Planned", "Approved", "Delayed"]))))
    links = db.execute(select(ProductMaterial, Material).join(Material, ProductMaterial.material_id == Material.id)).all()
    totals = {}
    for order in active:
        for bom, material in links:
            if bom.product.strip().casefold() == order.product.strip().casefold():
                key = material.code
                entry = totals.setdefault(key, {"material_code":material.code,"material_name":material.name,"unit":material.unit,"required":0.0,"available":material.on_hand,"shortage":0.0,"orders":[]})
                amount = order.quantity * bom.quantity_per_unit; entry["required"] += amount; entry["orders"].append(order.order_id)
    for entry in totals.values(): entry["shortage"] = max(0.0, entry["required"] - entry["available"]); entry["sufficient"] = entry["shortage"] == 0
    return {"basis":"Configured product-material quantities and active order quantities; products without a bill of materials are not estimated.","items":list(totals.values()),"unconfigured_products":sorted({o.product for o in active if not any(b.product.strip().casefold()==o.product.strip().casefold() for b,m in links)})}

@app.get("/api/audit")
def list_audit(limit: int = Query(default=100, ge=1, le=500), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit))
    return [{"id":r.id,"occurred_at":r.occurred_at,"actor":r.actor,"action":r.action,"entity_type":r.entity_type,"entity_id":r.entity_id,"details":r.details} for r in rows]

@app.post("/api/copilot")
def copilot(payload: dict, db: Session = Depends(get_db)):
    """Explainable production advisor grounded in stored factory records and the scheduling engine."""
    question = str(payload.get("question", "")).strip()
    if not question:
        raise HTTPException(422, "question is required")
    q = question.casefold()
    orders = list(db.scalars(select(Order).where(Order.status.not_in(["Completed", "Cancelled"])).order_by(Order.due_date)))
    today = date.today()
    late = [o for o in orders if o.due_date < today or o.status == "Delayed"]
    urgent = [o for o in orders if (o.due_date - today).days <= 3]
    materials = material_requirements(db)
    evidence = {"active_orders": len(orders), "overdue_or_delayed": len(late), "due_within_3_days": len(urgent)}
    recommendations = []
    recommendation = None

    planning_words = ["schedule", "planning", "plan", "recommend", "best", "proposal", "production", "خطة", "جدول", "تخطيط", "اقترح", "أفضل", "الافضل", "الإنتاج", "الإنتاج", "تصنيع", "أعمل إيه", "اعمل ايه"]
    if any(word in q for word in planning_words):
        try:
            candidates = []
            for goal in ("balanced", "deadline_first", "throughput"):
                candidate = solve_schedule(db, max_time_seconds=2, planning_goal=goal)
                sm = candidate.get("summary", {})
                item_by_id = {o.order_id: o for o in orders}
                weighted_late = sum((item_by_id.get(x.get("order_id")).priority if item_by_id.get(x.get("order_id")) else 1) for x in candidate.get("items", []) if x.get("late"))
                finish_dates = [x.get("finish_date") for x in candidate.get("items", []) if x.get("finish_date")]
                candidates.append({"goal": goal, "result": candidate, "score": (sm.get("unassigned", 0), sm.get("late", 0), weighted_late, max(finish_dates) if finish_dates else "9999-12-31")})
            candidates.sort(key=lambda c: c["score"])
            chosen = candidates[0]
            result = chosen["result"]
            summary = result.get("summary", {})
            goal_labels = {"balanced": "الخطة المتوازنة", "deadline_first": "خطة حماية مواعيد التسليم", "throughput": "خطة زيادة الإنتاجية"}
            if summary.get("orders", 0) == 0:
                answer = "لا توجد أوامر إنتاج مفتوحة يمكن جدولتها حاليًا. أضف الطلبات النشطة والماكينات المتاحة، ثم أعد إنشاء المقترح."
                next_action = "Import active orders and confirm machine availability before generating a plan."
            else:
                answer = f"بعد مقارنة 3 استراتيجيات جدولة، الاختيار المبدئي الأفضل حسب البيانات المتاحة هو {goal_labels[chosen['goal']]}: تم إسناد {summary.get('assigned', 0)} من {summary.get('orders', 0)} أمر، مع {summary.get('late', 0)} أمر معرض للتأخير و{summary.get('unassigned', 0)} أمر غير قابل للإسناد. هذا أفضل مرشح داخل القيود التي نمذجها النظام، وليس ضمانًا بأنه الحل الأمثل للمصنع بالكامل."
                next_action = "Review the comparison and flagged orders, then validate real shifts, routes, labor and material constraints with the production manager."
            recommendations = [
                "تم ترتيب المرشحين أولًا حسب عدد الطلبات غير المسندة، ثم الطلبات المتأخرة، ثم أولوية الطلبات المتأخرة، ثم تاريخ انتهاء آخر أمر.",
                "راجع كل أمر متأخر أو غير مسند قبل اعتماد الجدول، ولا تَعِد العميل بموعد جديد قبل التحقق من المصنع.",
                "أكد توافق نوع الماكينة والطاقة الإنتاجية ووقت التشغيل من بيانات المصنع الفعلية.",
                "الورديات ومهارات العمال وقيود المواد وتسلسل العمليات ليست جميعها قيودًا صلبة في محرك الطلبات الأساسي؛ لذلك يجب مراجعتها قبل الاعتماد."
            ]
            recommendation = {"solver": result.get("solver"), "optimality": result.get("optimality"), "orders": summary.get("orders", 0), "assigned": summary.get("assigned", 0), "late": summary.get("late", 0), "unassigned": summary.get("unassigned", 0), "runtime_seconds": None, "planning_goal": chosen["goal"], "next_action": next_action}
            evidence["strategy_comparison"] = [{"goal": c["goal"], "assigned": c["result"].get("summary", {}).get("assigned", 0), "late": c["result"].get("summary", {}).get("late", 0), "unassigned": c["result"].get("summary", {}).get("unassigned", 0), "ranking_score": list(c["score"])} for c in candidates]
            evidence["recommended_strategy"] = chosen["goal"]
            evidence["schedule_summary"] = summary
            evidence["schedule_preview"] = [{"order_id": x.get("order_id"), "machine_code": x.get("machine_code"), "start_date": x.get("start_date"), "finish_date": x.get("finish_date"), "late": x.get("late"), "warning": x.get("warning")} for x in result.get("items", [])[:30]]
            evidence["solver"] = result.get("solver")
            evidence["optimality"] = result.get("optimality")
        except Exception as exc:
            log.exception("Copilot schedule recommendation failed")
            answer = "تعذر إنشاء توصية جدول إنتاج من البيانات الحالية. راجع صلاحية بيانات الطلبات والماكينات ثم أعد المحاولة."
            evidence["planning_error"] = str(exc)
            recommendations = ["افحص وجود أوامر مفتوحة.", "تأكد من وجود ماكينات متاحة ومتوافقة مع أنواع الطلبات."]
    elif any(word in q for word in ["material", "stock", "خامة", "خامات", "مخزون", "نقص"]):
        shortages = [x for x in materials["items"] if x["shortage"] > 0]
        answer = f"يوجد {len(shortages)} خامة بها عجز وفق قوائم الخامات المسجلة." if shortages else "لا يظهر عجز في الخامات المُعرّفة. المنتجات التي ليس لها BOM لا يمكن الحكم على كفاية خاماتها بعد."
        evidence["shortages"] = shortages
        evidence["unconfigured_products"] = materials["unconfigured_products"]
        recommendations = ([f"راجع توفير الخامات الناقصة قبل جدولة المنتجات المتأثرة: {', '.join(x['material_code'] for x in shortages)}."] if shortages else ["أكمل قوائم BOM للمنتجات غير المعرّفة قبل اعتبار فحص المخزون مكتملًا."])
    elif any(word in q for word in ["late", "delay", "overdue", "متأخر", "تأخير"]):
        answer = f"يوجد {len(late)} أمر نشط متأخر أو حالته Delayed. ابدأ بالأعلى أولوية والأقرب إلى موعد التسليم، ثم تحقق من توافق الماكينة والطاقة والخامات."
        evidence["orders"] = [{"order_id": o.order_id, "product": o.product, "due_date": o.due_date.isoformat(), "status": o.status, "priority": o.priority, "quantity": o.quantity} for o in late]
        recommendations = [f"{o.order_id}: أولوية {o.priority}، موعد التسليم {o.due_date.isoformat()}، الحالة {o.status}." for o in sorted(late, key=lambda x: (x.due_date, -x.priority))[:8]]
    elif any(word in q for word in ["urgent", "soon", "3 day", "عاجل", "قريب", "ثلاث", "٣ أيام"]):
        answer = f"يوجد {len(urgent)} أمر نشط موعده خلال 3 أيام أو فات موعده. هذا مؤشر مخاطر أولي وليس تأكيدًا على إمكانية التسليم."
        evidence["orders"] = [{"order_id": o.order_id, "product": o.product, "due_date": o.due_date.isoformat(), "status": o.status, "priority": o.priority} for o in sorted(urgent, key=lambda x: (x.due_date, -x.priority))]
        recommendations = ["شغّل مقترح الجدولة ثم راجع أي أمر يظهر كمُعرّض للتأخير.", "تأكد من ساعات الوردية الفعلية والصيانة والخامات قبل الالتزام بموعد جديد."]
    else:
        summary = dashboard(db)
        answer = f"ملخص الحالة الحالية: {len(orders)} أمر نشط، {len(late)} متأخر أو معلّم بالتأخير، و{len(urgent)} موعده خلال 3 أيام أو أقل. أستطيع إنشاء توصية جدولة قابلة للمراجعة أو تحليل التأخير أو مخاطر الخامات بناءً على السجلات الموجودة."
        evidence["dashboard"] = summary
        recommendations = ["أفضل خطوة تالية: أنشئ مقترحًا من Decision Studio، ثم افحص التأخير والأوامر غير المسندة.", "أضف بيانات العمال والورديات وBOM وسجل الأداء لتحسين واقعية التوصية."]
    return {"answer": answer, "evidence": evidence, "mode": "optimization-grounded decision support", "recommendations": recommendations, "recommendation": recommendation, "mutated_data": False}


@app.post("/api/optimization/solve")
def optimization_solve(payload: dict = None, db: Session = Depends(get_db)):
    """Generate a non-persistent optimization proposal; never approves or mutates plans."""
    payload = payload or {}
    outage_machine = payload.get("outage_machine")
    try:
        outage_hours = int(payload.get("outage_hours", 0))
        workers_available = payload.get("workers_available")
        max_time_seconds = float(payload.get("max_time_seconds", 8))
        planning_goal = str(payload.get("planning_goal", "balanced"))
        if planning_goal not in {"balanced", "deadline_first", "throughput"}:
            raise ValueError("planning_goal must be balanced, deadline_first, or throughput")
        if outage_hours < 0 or outage_hours > 24 * 90:
            raise ValueError("outage_hours must be between 0 and 2160")
        if workers_available is not None:
            workers_available = int(workers_available)
            if workers_available < 0: raise ValueError("workers_available must be non-negative")
        if max_time_seconds <= 0: raise ValueError("max_time_seconds must be positive")
    except (TypeError, ValueError) as e:
        raise HTTPException(422, str(e))
    if outage_machine and not db.scalar(select(Machine).where(Machine.code == str(outage_machine))):
        raise HTTPException(404, "outage_machine not found")
    started = time.perf_counter()
    try:
        result = solve_schedule(db, workers_available, str(outage_machine) if outage_machine else None, outage_hours, max_time_seconds, planning_goal)
    except ValueError as e:
        raise HTTPException(422, str(e))
    elapsed = round(time.perf_counter() - started, 4)
    db.add(PlanningRun(solver=result.get("solver", "unknown"), elapsed_seconds=elapsed,
        order_count=result.get("summary", {}).get("orders", 0), assigned_count=result.get("summary", {}).get("assigned", 0),
        late_count=result.get("summary", {}).get("late", 0), notes="Non-persistent optimization proposal; timing captured for PoC measurement."))
    db.commit()
    result["runtime_seconds"] = elapsed
    goal_labels = {"balanced": "Balanced plan", "deadline_first": "Deadline protection", "throughput": "Throughput-focused plan"}
    result["planning_goal_label"] = goal_labels[planning_goal]
    summary = result.get("summary", {})
    if summary.get("orders", 0) == 0:
        result["recommendation_title"] = "No open orders to schedule"
        result["recommendation_summary"] = "Import or create active production orders before generating a schedule."
    elif summary.get("unassigned", 0):
        result["recommendation_title"] = "Resolve unassigned orders before approval"
        result["recommendation_summary"] = f"{summary.get('unassigned')} order(s) could not be assigned. Do not treat this proposal as feasible until the cause is resolved."
    elif summary.get("late", 0):
        result["recommendation_title"] = "Feasible draft with delivery risks"
        result["recommendation_summary"] = f"All orders were assigned, but {summary.get('late')} are projected late. Review due-date trade-offs before approval."
    else:
        result["recommendation_title"] = "Best current candidate for manager review"
        result["recommendation_summary"] = "All open orders were assigned without a projected late finish under the modelled constraints. Validate real shifts, labor, materials and process routes before approval."
    result["recommendations"] = []
    if summary.get("unassigned", 0): result["recommendations"].append("Resolve machine compatibility or add capacity for unassigned orders.")
    if summary.get("late", 0): result["recommendations"].append("Compare the Deadline protection objective and review the latest due dates first.")
    if workers_available is None: result["recommendations"].append("Enter available workforce and configure worker skills/shifts; current workforce handling is advisory, not a hard scheduling constraint.")
    if outage_machine and outage_hours: result["recommendations"].append(f"Scenario assumes {outage_machine} is unavailable for the first {outage_hours} hours from the planning start.")
    result["recommendations"].append("Manager approval is required. This is not an optimality guarantee unless the solver explicitly reports an optimal solution.")
    result["constraints_note"] = "Machine compatibility and non-overlap are enforced. The selected objective changes prioritization weights. Workforce count is currently advisory in the core order optimizer; detailed worker skills/shifts and multi-step routing are not hard constraints in this optimizer. Estimates use supplied hours or quantity/capacity. Hourly output is an approximation, not a validated factory shift calendar. This is a proposal, not execution authorization."
    return result


@app.get("/api/kpis/current")
def kpis_current(db: Session = Depends(get_db)):
    orders = list(db.scalars(select(Order)))
    actuals = list(db.scalars(select(ActualProduction)))
    plans = list(db.scalars(select(Plan)))
    latest = plans[0] if plans else None
    plan_items = list(db.scalars(select(PlanItem).where(PlanItem.plan_id == latest.id))) if latest else []
    completed = [o for o in orders if o.status == "Completed"]
    on_time = [o for o in completed if any(a.order_id == o.order_id and a.production_date <= o.due_date for a in actuals)]
    actual_order_ids = {a.order_id for a in actuals}
    durations = []
    for oid in actual_order_ids:
        rows = [a.production_date for a in actuals if a.order_id == oid]
        if rows: durations.append((max(rows)-min(rows)).days + 1)
    machine_hours = sum(float(a.downtime_hours or 0) for a in actuals)
    executions=list(db.scalars(select(OperationExecution)))
    runs=list(db.scalars(select(PlanningRun)))
    total_planned_hours=sum((e.planned_end_at-e.planned_start_at).total_seconds()/3600 for e in executions if e.planned_start_at and e.planned_end_at)
    total_actual_hours=sum((e.actual_end_at-e.actual_start_at).total_seconds()/3600 for e in executions if e.actual_start_at and e.actual_end_at)
    adherence=[e for e in executions if e.planned_start_at and e.planned_end_at and e.actual_start_at and e.actual_end_at]
    adherent=[e for e in adherence if abs((e.actual_start_at-e.planned_start_at).total_seconds())<=3600 and abs((e.actual_end_at-e.planned_end_at).total_seconds())<=3600]
    planning_seconds=round(sum(r.elapsed_seconds for r in runs)/len(runs),3) if runs else None
    machine_capacity_hours=sum(float(m.shift_hours)*5 for m in db.scalars(select(Machine).where(Machine.available.is_(True))))
    utilization=round(100*total_actual_hours/machine_capacity_hours,2) if machine_capacity_hours>0 and executions else None
    return {"as_of": date.today().isoformat(), "source_scope":"current local database; metrics omitted where evidence is insufficient", "metrics": {
        "total_orders": len(orders),
        "completed_orders": len(completed),
        "on_time_delivery_rate_percent": round(100*len(on_time)/len(completed),2) if completed else None,
        "on_time_delivery_sample_size": len(completed),
        "planning_time_seconds": planning_seconds,
        "machine_utilization_percent": utilization,
        "idle_time_hours": round(max(0,machine_capacity_hours-total_actual_hours),2) if machine_capacity_hours>0 and executions else None,
        "production_lead_time_days_average": round(sum(durations)/len(durations),2) if durations else None,
        "schedule_adherence_percent": round(100*len(adherent)/len(adherence),2) if adherence else None,
        "recorded_downtime_hours": round(machine_hours+sum(float(e.downtime_hours or 0) for e in executions),2),
        "operation_execution_records": len(executions),
        "planning_runs_recorded": len(runs),
        "latest_plan_id": latest.id if latest else None,
        "latest_plan_assigned_orders": sum(bool(i.machine_code) for i in plan_items),
        "latest_plan_late_orders": sum(bool(i.late) for i in plan_items),
        "latest_plan_unresolved_orders": sum(not i.machine_code for i in plan_items)
    }, "metric_limitations": {"on_time_delivery_rate_percent":"Proxy based on Completed status plus recorded production date; use actual shipment timestamps for true delivery KPI.","machine_utilization_percent":"Not computed without reliable available-time and runtime data.","idle_time_hours":"Not computed without machine-state timelines.","planning_time_seconds":"Not instrumented yet.","schedule_adherence_percent":"Not computed without planned-vs-actual operation timestamps."}}


@app.post("/api/kpis/baseline", status_code=201)
def kpi_baseline_save(payload: dict, db: Session = Depends(get_db)):
    metric = str(payload.get("metric", "")).strip()
    unit = str(payload.get("unit", "percent")).strip()
    period_label = str(payload.get("period_label", "manual baseline")).strip()
    source_note = str(payload.get("source_note", "")).strip()
    try: value = float(payload.get("value"))
    except (TypeError, ValueError): raise HTTPException(422, "value must be numeric")
    allowed = {"on_time_delivery_rate_percent", "machine_utilization_percent", "planning_time_seconds", "idle_time_hours", "production_lead_time_days_average", "schedule_adherence_percent"}
    if metric not in allowed: raise HTTPException(422, "Unsupported KPI metric")
    if value < 0: raise HTTPException(422, "value must be non-negative")
    row = KPIBaseline(metric=metric, value=value, unit=unit, period_label=period_label[:120], source_note=source_note[:2000])
    db.add(row); db.commit(); db.refresh(row)
    audit(db, "create", "kpi_baseline", row.id, f"{metric}={value} {unit}; period={period_label}"); db.commit()
    return {"id":row.id,"metric":row.metric,"value":row.value,"unit":row.unit,"period_label":row.period_label,"source_note":row.source_note}


@app.get("/api/kpis/compare")
def kpi_compare(db: Session = Depends(get_db)):
    current = kpis_current(db)["metrics"]
    baselines = list(db.scalars(select(KPIBaseline).order_by(KPIBaseline.created_at.desc())))
    seen=set(); rows=[]
    for b in baselines:
        if b.metric in seen: continue
        seen.add(b.metric); value=current.get(b.metric)
        delta = None if value is None else round(float(value)-b.value, 3)
        # Lower is better for time/idle/lead-time metrics.
        lower = any(x in b.metric for x in ("planning_time", "idle_time", "lead_time"))
        rows.append({"metric":b.metric,"baseline":b.value,"current":value,"unit":b.unit,"delta":delta,"direction":"improved" if delta is not None and ((delta < 0) if lower else (delta > 0)) else ("worsened" if delta is not None and delta != 0 else ("unchanged" if delta == 0 else "not measurable")),"baseline_period":b.period_label,"baseline_source_note":b.source_note})
    return {"comparisons":rows,"note":"Baseline values are user-entered. Current metrics may be null when reliable source data is unavailable; no missing values are fabricated."}


# --- IB-02 PoC capabilities: factory configuration, routings, workforce, performance and feedback ---
@app.get("/api/factories")
def factories_list(db: Session = Depends(get_db)):
    return [{"code":f.code,"name":f.name,"location":f.location,"notes":f.notes,
             "machine_codes":[x.machine_code for x in db.scalars(select(FactoryMachineLink).where(FactoryMachineLink.factory_code==f.code))]}
            for f in db.scalars(select(Factory).order_by(Factory.code))]

@app.post("/api/factories", status_code=201)
def factory_create(payload: dict, db: Session = Depends(get_db)):
    code=str(payload.get("code", "")).strip(); name=str(payload.get("name", "")).strip()
    if not code or not name: raise HTTPException(422, "code and name are required")
    obj=Factory(code=code, name=name, location=str(payload.get("location", "")).strip() or None, notes=str(payload.get("notes", "")).strip() or None)
    db.add(obj)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409, "Factory code already exists")
    audit(db,"create","factory",code,"Factory configuration created"); db.commit()
    return {"code":obj.code,"name":obj.name,"location":obj.location,"notes":obj.notes}

@app.post("/api/factories/{factory_code}/machines", status_code=201)
def factory_link_machine(factory_code: str, payload: dict, db: Session = Depends(get_db)):
    if not db.scalar(select(Factory).where(Factory.code==factory_code)): raise HTTPException(404,"Factory not found")
    code=str(payload.get("machine_code", "")).strip()
    if not db.scalar(select(Machine).where(Machine.code==code)): raise HTTPException(404,"Machine not found")
    existing=db.scalar(select(FactoryMachineLink).where(FactoryMachineLink.factory_code==factory_code,FactoryMachineLink.machine_code==code))
    if existing: return {"factory_code":factory_code,"machine_code":code,"already_linked":True}
    row=FactoryMachineLink(factory_code=factory_code,machine_code=code); db.add(row); db.commit()
    audit(db,"link","factory_machine",f"{factory_code}:{code}","Machine linked to factory"); db.commit()
    return {"factory_code":factory_code,"machine_code":code}

@app.get("/api/workflows")
def workflow_list(factory_code: str | None = None, product: str | None = None, db: Session = Depends(get_db)):
    q=select(ProductProcessStep)
    if factory_code: q=q.where(ProductProcessStep.factory_code==factory_code)
    if product: q=q.where(ProductProcessStep.product==product)
    return [{"id":x.id,"factory_code":x.factory_code,"product":x.product,"sequence":x.sequence,"process_code":x.process_code,
             "required_machine_type":x.required_machine_type,"estimated_hours":x.estimated_hours,"workers_required":x.workers_required,"notes":x.notes}
            for x in db.scalars(q.order_by(ProductProcessStep.factory_code,ProductProcessStep.product,ProductProcessStep.sequence))]

@app.post("/api/workflows", status_code=201)
def workflow_step_create(payload: dict, db: Session = Depends(get_db)):
    try:
        factory_code=str(payload["factory_code"]).strip(); product=str(payload["product"]).strip(); process_code=str(payload["process_code"]).strip()
        sequence=int(payload["sequence"]); hours=float(payload["estimated_hours"]); workers=int(payload.get("workers_required",1)); machine_type=str(payload["required_machine_type"]).strip()
    except (KeyError,TypeError,ValueError): raise HTTPException(422,"factory_code, product, sequence, process_code, required_machine_type and estimated_hours are required")
    if not factory_code or not product or not process_code or not machine_type or sequence<1 or hours<=0 or workers<1: raise HTTPException(422,"Invalid process-step values")
    if not db.scalar(select(Factory).where(Factory.code==factory_code)): raise HTTPException(404,"Factory not found; create the factory first")
    row=ProductProcessStep(factory_code=factory_code,product=product,sequence=sequence,process_code=process_code,required_machine_type=machine_type,estimated_hours=hours,workers_required=workers,notes=str(payload.get("notes", "")).strip() or None)
    db.add(row); db.commit(); db.refresh(row); audit(db,"create","workflow_step",row.id,f"{factory_code}/{product}/{sequence}/{process_code}"); db.commit()
    return {"id":row.id,"factory_code":row.factory_code,"product":row.product,"sequence":row.sequence,"process_code":row.process_code,"required_machine_type":row.required_machine_type,"estimated_hours":row.estimated_hours,"workers_required":row.workers_required,"notes":row.notes}

@app.delete("/api/workflows/{step_id}", status_code=204)
def workflow_step_delete(step_id: int, db: Session = Depends(get_db)):
    row=db.get(ProductProcessStep,step_id)
    if not row: raise HTTPException(404,"Workflow step not found")
    audit(db,"delete","workflow_step",step_id,"Workflow step deleted"); db.delete(row); db.commit()

@app.post("/api/workflows/preview")
def workflow_preview(payload: dict, db: Session = Depends(get_db)):
    factory_code=str(payload.get("factory_code", "")).strip(); order_id=str(payload.get("order_id", "")).strip()
    factory=db.scalar(select(Factory).where(Factory.code==factory_code)); order=db.scalar(select(Order).where(Order.order_id==order_id))
    if not factory: raise HTTPException(404,"Factory not found")
    if not order: raise HTTPException(404,"Order not found")
    steps=list(db.scalars(select(ProductProcessStep).where(ProductProcessStep.factory_code==factory_code,ProductProcessStep.product==order.product).order_by(ProductProcessStep.sequence)))
    if not steps: raise HTTPException(422,"No configured workflow for this factory/product. Configure process steps; no route is guessed.")
    linked={x.machine_code for x in db.scalars(select(FactoryMachineLink).where(FactoryMachineLink.factory_code==factory_code))}
    machines=list(db.scalars(select(Machine).where(Machine.available.is_(True))))
    if linked: machines=[m for m in machines if m.code in linked]
    now=datetime.now()
    active_downtime={x.machine_code for x in db.scalars(select(MachineDowntimeEvent)) if x.start_at<=now and (x.end_at is None or x.end_at>now)}
    machines=[m for m in machines if m.code not in active_downtime]
    worker_rows=list(db.scalars(select(Worker)))
    available_workers=sum(1 for w in worker_rows if w.available) if worker_rows else None
    cursor=datetime.combine(date.today(),datetime.min.time()).replace(hour=8); result=[]; unresolved=[]
    for step in steps:
        if available_workers is not None and step.workers_required > available_workers:
            unresolved.append({"process_code":step.process_code,"reason":f"Requires {step.workers_required} workers but only {available_workers} workers are marked available"}); continue
        candidates=[m for m in machines if m.machine_type.lower()==step.required_machine_type.lower()]
        if not candidates:
            unresolved.append({"process_code":step.process_code,"reason":"No available compatible machine linked to this factory (or machine has an active downtime event)"}); continue
        ranked=[]
        for candidate in candidates:
            perf=db.scalar(select(MachinePerformanceRecord).where(MachinePerformanceRecord.machine_code==candidate.code,MachinePerformanceRecord.product.in_([order.product,"*"]),MachinePerformanceRecord.process_code.in_([step.process_code,"*"])).order_by(MachinePerformanceRecord.recorded_at.desc()))
            factor=perf.speed_factor if perf else 1.0
            ranked.append((step.estimated_hours*factor,candidate,factor))
        hours,m,factor=min(ranked,key=lambda item:(item[0],item[1].code))
        finish=cursor+timedelta(hours=hours)
        result.append({"sequence":step.sequence,"process_code":step.process_code,"machine_code":m.code,"start_at":cursor.isoformat(),"finish_at":finish.isoformat(),"estimated_hours":round(hours,2),"workers_required":step.workers_required,"performance_factor":factor,"reason":"Configured workflow step; duration adjusted only by a recorded performance factor when available."})
        cursor=finish
    return {"factory_code":factory_code,"order_id":order_id,"product":order.product,"proposal_only":True,"persisted":False,"steps":result,"unresolved":unresolved,"workers_available":available_workers,"note":"Sequential workflow preview; production calendars, setup/changeover and worker shift matching must be validated before operational use."}

@app.get("/api/workers")
def workers_list(db: Session = Depends(get_db)):
    return [{"worker_code":w.worker_code,"name":w.name,"skills":[x.strip() for x in w.skills.split(",") if x.strip()],"available":w.available,"shift_hours":w.shift_hours,"notes":w.notes} for w in db.scalars(select(Worker).order_by(Worker.worker_code))]

@app.post("/api/workers", status_code=201)
def worker_create(payload: dict, db: Session = Depends(get_db)):
    code=str(payload.get("worker_code", "")).strip(); name=str(payload.get("name", "")).strip(); skills=payload.get("skills",[])
    if not code or not name: raise HTTPException(422,"worker_code and name are required")
    if not isinstance(skills,(list,str)): raise HTTPException(422,"skills must be a list or comma-separated string")
    if isinstance(skills,list): skills=",".join(str(x).strip() for x in skills if str(x).strip())
    try: shift=float(payload.get("shift_hours",8))
    except (TypeError,ValueError): raise HTTPException(422,"shift_hours must be numeric")
    if not 0 < shift <= 24: raise HTTPException(422,"shift_hours must be between 0 and 24")
    row=Worker(worker_code=code,name=name,skills=skills,available=bool(payload.get("available",True)),shift_hours=shift,notes=payload.get("notes")); db.add(row)
    try: db.commit()
    except IntegrityError: db.rollback(); raise HTTPException(409,"Worker code already exists")
    audit(db,"create","worker",code,f"skills={skills}; available={row.available}"); db.commit()
    return {"worker_code":row.worker_code,"name":row.name,"skills":[x for x in skills.split(",") if x],"available":row.available,"shift_hours":row.shift_hours,"notes":row.notes}

@app.post("/api/orders/{order_id}/planning-profile")
def order_planning_profile(order_id: str, payload: dict, db: Session = Depends(get_db)):
    order=db.scalar(select(Order).where(Order.order_id==order_id))
    if not order: raise HTTPException(404,"Order not found")
    try: workers=int(payload.get("workers_required",1))
    except (TypeError,ValueError): raise HTTPException(422,"workers_required must be an integer")
    if workers<1: raise HTTPException(422,"workers_required must be at least 1")
    row=db.scalar(select(OrderPlanningProfile).where(OrderPlanningProfile.order_id==order_id))
    if row is None: row=OrderPlanningProfile(order_id=order_id); db.add(row)
    row.workers_required=workers; row.preferred_process=str(payload.get("preferred_process", "")).strip() or None; row.notes=str(payload.get("notes", "")).strip() or None
    db.commit(); audit(db,"update","order_planning_profile",order_id,f"workers_required={workers}"); db.commit()
    return {"order_id":order_id,"workers_required":workers,"preferred_process":row.preferred_process,"notes":row.notes}

@app.post("/api/performance", status_code=201)
def performance_create(payload: dict, db: Session = Depends(get_db)):
    code=str(payload.get("machine_code", "")).strip()
    if not db.scalar(select(Machine).where(Machine.code==code)): raise HTTPException(404,"Machine not found")
    try: factor=float(payload.get("speed_factor",1)); samples=int(payload.get("sample_count",1))
    except (TypeError,ValueError): raise HTTPException(422,"speed_factor and sample_count must be numeric")
    if factor<=0 or samples<1: raise HTTPException(422,"speed_factor must be positive and sample_count >= 1")
    row=MachinePerformanceRecord(machine_code=code,product=str(payload.get("product","*")).strip() or "*",process_code=str(payload.get("process_code","*")).strip() or "*",speed_factor=factor,sample_count=samples,source_note=str(payload.get("source_note", "")).strip())
    db.add(row); db.commit(); db.refresh(row); audit(db,"create","machine_performance",row.id,f"machine={code}; factor={factor}; samples={samples}"); db.commit()
    return {"id":row.id,"machine_code":row.machine_code,"product":row.product,"process_code":row.process_code,"speed_factor":row.speed_factor,"sample_count":row.sample_count,"source_note":row.source_note,"recorded_at":row.recorded_at.isoformat()}

@app.get("/api/performance")
def performance_list(db: Session = Depends(get_db)):
    return [{"id":r.id,"machine_code":r.machine_code,"product":r.product,"process_code":r.process_code,"speed_factor":r.speed_factor,"sample_count":r.sample_count,"source_note":r.source_note,"recorded_at":r.recorded_at.isoformat()} for r in db.scalars(select(MachinePerformanceRecord).order_by(MachinePerformanceRecord.recorded_at.desc()))]

@app.post("/api/machine-downtime", status_code=201)
def downtime_create(payload: dict, db: Session = Depends(get_db)):
    code=str(payload.get("machine_code", "")).strip()
    if not db.scalar(select(Machine).where(Machine.code==code)): raise HTTPException(404,"Machine not found")
    try: start=datetime.fromisoformat(str(payload.get("start_at"))); end=datetime.fromisoformat(str(payload["end_at"])) if payload.get("end_at") else None
    except (TypeError,ValueError,KeyError): raise HTTPException(422,"start_at and optional end_at must be ISO datetimes")
    if end and end<start: raise HTTPException(422,"end_at must be after start_at")
    row=MachineDowntimeEvent(machine_code=code,start_at=start,end_at=end,reason=str(payload.get("reason","Unplanned downtime")),status="Closed" if end else "Open")
    db.add(row); db.commit(); db.refresh(row)
    audit(db,"create","machine_downtime",row.id,f"machine={code}; status={row.status}"); db.commit()
    return {"id":row.id,"machine_code":code,"start_at":start.isoformat(),"end_at":end.isoformat() if end else None,"reason":row.reason,"status":row.status}

@app.get("/api/machine-downtime")
def downtime_list(db: Session = Depends(get_db)):
    return [{"id":r.id,"machine_code":r.machine_code,"start_at":r.start_at.isoformat(),"end_at":r.end_at.isoformat() if r.end_at else None,"reason":r.reason,"status":r.status} for r in db.scalars(select(MachineDowntimeEvent).order_by(MachineDowntimeEvent.start_at.desc()))]

@app.post("/api/operations/execution", status_code=201)
def operation_execution_create(payload: dict, db: Session = Depends(get_db)):
    order_id=str(payload.get("order_id", "")).strip(); machine_code=str(payload.get("machine_code", "")).strip()
    if not db.scalar(select(Order).where(Order.order_id==order_id)): raise HTTPException(404,"Order not found")
    if not db.scalar(select(Machine).where(Machine.code==machine_code)): raise HTTPException(404,"Machine not found")
    def parse_dt(key):
        v=payload.get(key)
        if not v: return None
        try: return datetime.fromisoformat(str(v))
        except ValueError: raise HTTPException(422,f"{key} must be an ISO datetime")
    try: planned_qty=int(payload.get("planned_quantity",0)); actual_qty=int(payload.get("actual_quantity",0)); downtime=float(payload.get("downtime_hours",0))
    except (TypeError,ValueError): raise HTTPException(422,"Quantities and downtime must be numeric")
    if min(planned_qty,actual_qty,downtime)<0: raise HTTPException(422,"Quantities and downtime must be non-negative")
    row=OperationExecution(order_id=order_id,process_code=str(payload.get("process_code","Production")),machine_code=machine_code,planned_start_at=parse_dt("planned_start_at"),planned_end_at=parse_dt("planned_end_at"),actual_start_at=parse_dt("actual_start_at"),actual_end_at=parse_dt("actual_end_at"),planned_quantity=planned_qty,actual_quantity=actual_qty,downtime_hours=downtime,status=str(payload.get("status","Recorded")),delay_reason=payload.get("delay_reason"))
    if row.actual_start_at and row.actual_end_at and row.actual_end_at<row.actual_start_at: raise HTTPException(422,"actual_end_at must be after actual_start_at")
    db.add(row); db.commit(); db.refresh(row); audit(db,"create","operation_execution",row.id,f"order={order_id}; machine={machine_code}; actual={actual_qty}"); db.commit()
    return {"id":row.id,"order_id":row.order_id,"process_code":row.process_code,"machine_code":row.machine_code,"planned_start_at":row.planned_start_at.isoformat() if row.planned_start_at else None,"planned_end_at":row.planned_end_at.isoformat() if row.planned_end_at else None,"actual_start_at":row.actual_start_at.isoformat() if row.actual_start_at else None,"actual_end_at":row.actual_end_at.isoformat() if row.actual_end_at else None,"planned_quantity":row.planned_quantity,"actual_quantity":row.actual_quantity,"downtime_hours":row.downtime_hours,"status":row.status,"delay_reason":row.delay_reason}

@app.get("/api/operations/execution")
def operation_executions_list(db: Session = Depends(get_db)):
    return [{"id":r.id,"order_id":r.order_id,"process_code":r.process_code,"machine_code":r.machine_code,"planned_start_at":r.planned_start_at.isoformat() if r.planned_start_at else None,"planned_end_at":r.planned_end_at.isoformat() if r.planned_end_at else None,"actual_start_at":r.actual_start_at.isoformat() if r.actual_start_at else None,"actual_end_at":r.actual_end_at.isoformat() if r.actual_end_at else None,"planned_quantity":r.planned_quantity,"actual_quantity":r.actual_quantity,"downtime_hours":r.downtime_hours,"status":r.status,"delay_reason":r.delay_reason} for r in db.scalars(select(OperationExecution).order_by(OperationExecution.id.desc()))]

@app.get("/api/poc/roadmap")
def poc_roadmap():
    """Legacy API retained for compatibility; the roadmap is intentionally not shown in the UI."""
    return {"duration_months": 6, "budget_ceiling_usd": 30000, "phases": [], "evaluation_kpis": ["on_time_delivery_rate_percent", "machine_utilization_percent", "planning_time_seconds", "idle_time_hours", "production_lead_time_days_average", "schedule_adherence_percent"], "guardrails": []}


@app.get("/api/closed-loop/feedback")
def closed_loop_feedback(db: Session = Depends(get_db)):
    executions=list(db.scalars(select(OperationExecution)))
    if not executions:
        return {"status":"insufficient_data","records":0,"feedback":[],"note":"Record planned and actual operation timestamps first. No feedback is inferred without evidence."}
    feedback=[]
    for e in executions:
        duration_delta=None
        if e.planned_start_at and e.planned_end_at and e.actual_start_at and e.actual_end_at:
            planned=(e.planned_end_at-e.planned_start_at).total_seconds()/3600
            actual=(e.actual_end_at-e.actual_start_at).total_seconds()/3600
            duration_delta=round(actual-planned,2)
        qty_delta=e.actual_quantity-e.planned_quantity
        feedback.append({"execution_id":e.id,"order_id":e.order_id,"process_code":e.process_code,"machine_code":e.machine_code,"quantity_variance":qty_delta,"duration_variance_hours":duration_delta,"downtime_hours":e.downtime_hours,"delay_reason":e.delay_reason,"suggested_follow_up":"Review machine/process performance factor" if duration_delta is not None and duration_delta>0 else ("Review yield or plan quantity" if qty_delta<0 else "No automatic change; review records with production manager")})
    return {"status":"feedback_available","records":len(executions),"feedback":feedback,"note":"Feedback is decision support only. Historical performance records are not auto-updated from a single execution; validate data before changing future planning factors."}


@app.get("/api/reports/summary")
def reports(db:Session=Depends(get_db)):
    return {"dashboard":dashboard(db),"orders":[order_out(o) for o in db.scalars(select(Order).order_by(Order.due_date))],"actual":[{"order_id":a.order_id,"production_date":a.production_date,"planned_quantity":a.planned_quantity,"actual_quantity":a.actual_quantity,"variance":a.actual_quantity-a.planned_quantity} for a in db.scalars(select(ActualProduction))]}

@app.get("/api/reports/orders.csv")
def export_orders(db:Session=Depends(get_db)):
    output=io.StringIO(); fields=["order_id","product","quantity","due_date","priority","machine_type","estimated_hours","customer","status"]
    w=csv.DictWriter(output,fieldnames=fields); w.writeheader()
    for o in db.scalars(select(Order)): w.writerow({f:getattr(o,f) for f in fields})
    return StreamingResponse(iter([output.getvalue()]),media_type="text/csv",headers={"Content-Disposition":"attachment; filename=smartplan_orders.csv"})
