import io, os, tempfile
from pathlib import Path
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tempfile.gettempdir()) / ('smartplan-tests-' + str(os.getpid()) + '.db')}"
Path(Path(tempfile.gettempdir()) / ('smartplan-tests-' + str(os.getpid()) + '.db')).unlink(missing_ok=True)
from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db
from app.database import init_db

init_db()
client=TestClient(app)

def test_health():
    r=client.get("/api/health")
    assert r.status_code==200 and r.json()["status"]=="ok"

def test_order_create_validation_and_duplicate():
    payload={"order_id":"TEST-100","product":"Mesh panel","quantity":20,"due_date":"2026-12-01","priority":4,"machine_type":"mesh"}
    r=client.post("/api/orders",json=payload)
    assert r.status_code==201
    assert client.post("/api/orders",json=payload).status_code==409
    bad={**payload,"order_id":"TEST-BAD","quantity":0}
    assert client.post("/api/orders",json=bad).status_code==422

def test_machine_creation_and_duplicate():
    p={"code":"MESH-TEST","name":"Mesh line test","machine_type":"mesh","capacity_per_day":100,"available":True,"shift_hours":8}
    assert client.post("/api/machines",json=p).status_code==201
    assert client.post("/api/machines",json=p).status_code==409

def test_import_preview_missing_columns_and_valid_csv():
    missing=client.post("/api/import/preview",files={"file":("bad.csv",b"foo,bar\n1,2","text/csv")})
    assert missing.status_code==200 and missing.json()["missing_columns"]
    data=b"order_id,product,quantity,due_date\nCSV-1,Wire mesh,30,2026-12-10\nCSV-2,Mesh,0,2026-12-11\n"
    r=client.post("/api/import/preview",files={"file":("orders.csv",data,"text/csv")})
    assert r.status_code==200
    assert r.json()["rows"][0]["valid"] is True
    assert r.json()["rows"][1]["valid"] is False

def test_planning_and_approval_workflow():
    client.post("/api/machines",json={"code":"PLAN-M","name":"Plan machine","machine_type":"general","capacity_per_day":100,"available":True,"shift_hours":8})
    client.post("/api/orders",json={"order_id":"PLAN-ORDER","product":"Mesh roll","quantity":10,"due_date":"2027-01-01","priority":5,"machine_type":"general"})
    p=client.post("/api/plans/generate")
    assert p.status_code==201 and p.json()["status"]=="Proposed"
    pid=p.json()["id"]
    assert any(i["order_id"]=="PLAN-ORDER" and i["machine_code"] for i in p.json()["items"])
    assert client.post(f"/api/plans/{pid}/approve").json()["status"]=="Approved"
    assert client.post(f"/api/plans/{pid}/approve").status_code==409

def test_actual_recording():
    client.post("/api/machines",json={"code":"ACT-M","name":"Actual machine","machine_type":"general","capacity_per_day":50})
    client.post("/api/orders",json={"order_id":"ACT-ORDER","product":"Wire mesh","quantity":5,"due_date":"2027-01-01"})
    r=client.post("/api/actual",json={"order_id":"ACT-ORDER","production_date":"2026-10-16","machine_code":"ACT-M","planned_quantity":5,"actual_quantity":5})
    assert r.status_code==200 and r.json()["completion_percent"]==100


def test_material_inventory_bom_requirements_and_copilot():
    material = {"code":"MAT-TEST-01","name":"Test wire","unit":"kg","on_hand":5,"reorder_point":2}
    r = client.post("/api/materials", json=material)
    assert r.status_code == 201
    assert client.post("/api/materials", json=material).status_code == 409
    bom = client.post("/api/materials/bom", json={"product":"Test mesh product","material_code":"MAT-TEST-01","quantity_per_unit":0.5})
    assert bom.status_code == 201
    order = client.post("/api/orders", json={"order_id":"MAT-ORDER-01","product":"Test mesh product","quantity":20,"due_date":"2027-02-01"})
    assert order.status_code == 201
    req = client.get("/api/materials/requirements").json()
    item = next(x for x in req["items"] if x["material_code"] == "MAT-TEST-01")
    assert item["required"] == 10 and item["shortage"] == 5
    answer = client.post("/api/copilot", json={"question":"Do we have material shortages?"})
    assert answer.status_code == 200 and answer.json()["mode"] == "optimization-grounded decision support"
    assert answer.json()["mutated_data"] is False

def test_audit_endpoint_records_key_changes():
    r = client.post("/api/materials", json={"code":"AUDIT-MAT","name":"Audit material","unit":"kg","on_hand":1,"reorder_point":0})
    assert r.status_code == 201
    logs = client.get("/api/audit?limit=20")
    assert logs.status_code == 200
    assert any(x["entity_id"] == "AUDIT-MAT" and x["entity_type"] == "material" for x in logs.json())

def test_unavailable_machine_not_assigned():
    client.post("/api/machines", json={"code":"OFF-MACHINE","name":"Offline mesh machine","machine_type":"specialmesh","capacity_per_day":50,"available":False})
    client.post("/api/orders", json={"order_id":"OFF-ORDER","product":"Mesh","quantity":5,"due_date":"2027-01-01","machine_type":"specialmesh"})
    p = client.post("/api/plans/generate")
    item = next(x for x in p.json()["items"] if x["order_id"] == "OFF-ORDER")
    assert item["machine_code"] is None and item["warning"]

def test_disruption_simulation_is_non_persistent_and_reassigns():
    client.post("/api/machines", json={"code":"SIM-M1","name":"Simulation machine 1","machine_type":"general","capacity_per_day":10,"available":True,"shift_hours":8})
    client.post("/api/machines", json={"code":"SIM-M2","name":"Simulation machine 2","machine_type":"general","capacity_per_day":100,"available":True,"shift_hours":8})
    client.post("/api/orders", json={"order_id":"SIM-ORDER","product":"Sample product","quantity":10,"due_date":"2027-01-01","priority":5,"machine_type":"general"})
    before = len(client.get("/api/plans").json())
    r = client.post("/api/plans/simulate-disruption", json={"machine_code":"SIM-M2","outage_days":2})
    assert r.status_code == 200
    data = r.json()
    assert data["persisted"] is False
    assert data["machine_code"] == "SIM-M2"
    assert data["outage_days"] == 2
    assert any(i["order_id"] == "SIM-ORDER" and i["machine_code"] for i in data["items"])
    assert len(client.get("/api/plans").json()) == before
    assert client.post("/api/plans/simulate-disruption", json={"machine_code":"SIM-M2","outage_days":0}).status_code == 422

def test_optimization_proposal_is_non_persistent_and_reports_constraints():
    client.post("/api/machines", json={"code":"OPT-M1","name":"Optimization machine","machine_type":"general","capacity_per_day":100,"available":True,"shift_hours":8})
    client.post("/api/orders", json={"order_id":"OPT-ORDER-1","product":"Sample product","quantity":20,"due_date":"2027-03-01","priority":4,"machine_type":"general"})
    before=len(client.get("/api/plans").json())
    r=client.post("/api/optimization/solve",json={"workers_available":5})
    assert r.status_code==200
    data=r.json()
    assert data["persisted"] is False
    assert data["summary"]["assigned"] >= 1
    assert "workforce count is currently advisory" in data["constraints_note"].lower()
    assert data["planning_goal_label"] == "Balanced plan"
    assert data["recommendation_title"]
    assert isinstance(data["recommendations"], list)
    deadline = client.post("/api/optimization/solve", json={"planning_goal":"deadline_first"})
    assert deadline.status_code == 200 and deadline.json()["planning_goal"] == "deadline_first"
    assert len(client.get("/api/plans").json())==before
    assert client.post("/api/optimization/solve",json={"workers_available":-1}).status_code==422
    assert client.post("/api/optimization/solve",json={"planning_goal":"not-a-goal"}).status_code==422


def test_kpi_baseline_and_comparison_endpoint():
    r=client.post("/api/kpis/baseline",json={"metric":"planning_time_seconds","value":3600,"unit":"seconds","period_label":"manual historical baseline","source_note":"Measured by production manager"})
    assert r.status_code==201
    comparison=client.get("/api/kpis/compare")
    assert comparison.status_code==200
    item=next(x for x in comparison.json()["comparisons"] if x["metric"]=="planning_time_seconds")
    assert item["baseline"]==3600 and item["current"] is not None
    assert item["current"] >= 0
    assert client.post("/api/kpis/baseline",json={"metric":"unknown_metric","value":5}).status_code==422


def test_factory_workflow_preview_and_performance_factor():
    assert client.post("/api/factories", json={"code":"FACT-TEST","name":"Factory Test"}).status_code == 201
    assert client.post("/api/machines", json={"code":"WF-M1","name":"Workflow machine","machine_type":"cut","capacity_per_day":100,"available":True}).status_code == 201
    assert client.post("/api/factories/FACT-TEST/machines", json={"machine_code":"WF-M1"}).status_code == 201
    assert client.post("/api/orders", json={"order_id":"WF-ORDER","product":"Wire panel","quantity":10,"due_date":"2027-04-01"}).status_code == 201
    assert client.post("/api/workflows", json={"factory_code":"FACT-TEST","product":"Wire panel","sequence":1,"process_code":"CUT","required_machine_type":"cut","estimated_hours":4,"workers_required":1}).status_code == 201
    assert client.post("/api/performance", json={"machine_code":"WF-M1","product":"Wire panel","process_code":"CUT","speed_factor":1.15,"sample_count":8,"source_note":"Verified sample history"}).status_code == 201
    result=client.post("/api/workflows/preview", json={"factory_code":"FACT-TEST","order_id":"WF-ORDER"})
    assert result.status_code == 200
    data=result.json()
    assert data["proposal_only"] is True and data["steps"][0]["machine_code"] == "WF-M1"
    assert data["steps"][0]["estimated_hours"] == 4.6

def test_workforce_downtime_execution_and_closed_loop():
    assert client.post("/api/workers", json={"worker_code":"W-TEST","name":"Test Worker","skills":["welding"],"available":True,"shift_hours":8}).status_code == 201
    assert client.post("/api/machines", json={"code":"FB-M1","name":"Feedback machine","machine_type":"general","capacity_per_day":50}).status_code == 201
    assert client.post("/api/orders", json={"order_id":"FB-ORDER","product":"Mesh","quantity":5,"due_date":"2027-04-01"}).status_code == 201
    assert client.post("/api/orders/FB-ORDER/planning-profile", json={"workers_required":1}).status_code == 200
    event=client.post("/api/operations/execution", json={"order_id":"FB-ORDER","machine_code":"FB-M1","process_code":"WELD","planned_start_at":"2026-10-16T08:00:00","planned_end_at":"2026-10-16T10:00:00","actual_start_at":"2026-10-16T08:30:00","actual_end_at":"2026-10-16T11:00:00","planned_quantity":5,"actual_quantity":4,"downtime_hours":0.5})
    assert event.status_code == 201
    feedback=client.get("/api/closed-loop/feedback").json()
    assert feedback["records"] >= 1 and any(x["order_id"]=="FB-ORDER" for x in feedback["feedback"])

def test_legacy_roadmap_api_is_not_used_for_ui():
    # Legacy API remains for compatibility, but roadmap content is removed from the UI.
    data=client.get("/api/poc/roadmap").json()
    assert data["phases"] == []
    template=(Path(__file__).parents[1] / "app" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "IB-02 PoC roadmap" not in template
    assert 'id="poc-roadmap"' not in template


def test_production_advisor_compares_strategies_and_does_not_mutate_plan():
    response = client.post("/api/copilot", json={"question":"Recommend the best production schedule"})
    assert response.status_code == 200
    data = response.json()
    assert data["mode"] == "optimization-grounded decision support"
    assert data["recommendation"] is not None
    assert len(data["evidence"]["strategy_comparison"]) == 3
    assert data["mutated_data"] is False


def test_demo_seed_refuses_to_mix_with_existing_production_data():
    # Other tests create orders/machines in this test database; seeding must be refused safely.
    response = client.post("/api/demo/seed")
    assert response.status_code == 409
    assert "already contains orders or machines" in response.json()["detail"]
