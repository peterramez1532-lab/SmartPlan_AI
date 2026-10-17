from sqlalchemy import String, Integer, Float, Date, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime, date
from app.database import Base

class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    product: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(Integer)
    order_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date] = mapped_column(Date, index=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    machine_type: Mapped[str] = mapped_column(String(100), default="general")
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    customer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="Pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Machine(Base):
    __tablename__ = "machines"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    machine_type: Mapped[str] = mapped_column(String(100), index=True)
    capacity_per_day: Mapped[float] = mapped_column(Float)
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    shift_hours: Mapped[float] = mapped_column(Float, default=8)
    maintenance_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    maintenance_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    status: Mapped[str] = mapped_column(String(30), default="Proposed")
    rules: Mapped[str] = mapped_column(Text, default="Priority, deadline, capacity, compatibility, and machine availability heuristic")

class PlanItem(Base):
    __tablename__ = "plan_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("plans.id"), index=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    machine_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    finish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    late: Mapped[bool] = mapped_column(Boolean, default=False)
    reason: Mapped[str] = mapped_column(Text)
    warning: Mapped[str | None] = mapped_column(Text, nullable=True)

class ActualProduction(Base):
    __tablename__ = "actual_production"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    production_date: Mapped[date]
    machine_code: Mapped[str] = mapped_column(String(80))
    planned_quantity: Mapped[int] = mapped_column(Integer)
    actual_quantity: Mapped[int] = mapped_column(Integer)
    downtime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    delay_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Material(Base):
    __tablename__ = "materials"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(30), default="kg")
    on_hand: Mapped[float] = mapped_column(Float, default=0)
    reorder_point: Mapped[float] = mapped_column(Float, default=0)
    supplier: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class ProductMaterial(Base):
    __tablename__ = "product_materials"
    id: Mapped[int] = mapped_column(primary_key=True)
    product: Mapped[str] = mapped_column(String(200), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id", ondelete="CASCADE"), index=True)
    quantity_per_unit: Mapped[float] = mapped_column(Float)

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="local-user")
    action: Mapped[str] = mapped_column(String(100), index=True)
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(120))
    details: Mapped[str] = mapped_column(Text, default="")

class KPIBaseline(Base):
    __tablename__ = "kpi_baselines"
    id: Mapped[int] = mapped_column(primary_key=True)
    metric: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30), default="percent")
    period_label: Mapped[str] = mapped_column(String(120), default="manual baseline")
    source_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# Extensible factory/process configuration for multi-factory deployment.
class Factory(Base):
    __tablename__ = "factories"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class FactoryMachineLink(Base):
    __tablename__ = "factory_machine_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    factory_code: Mapped[str] = mapped_column(String(80), index=True)
    machine_code: Mapped[str] = mapped_column(String(80), index=True)

class ProductProcessStep(Base):
    __tablename__ = "product_process_steps"
    id: Mapped[int] = mapped_column(primary_key=True)
    factory_code: Mapped[str] = mapped_column(String(80), index=True)
    product: Mapped[str] = mapped_column(String(200), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    process_code: Mapped[str] = mapped_column(String(100))
    required_machine_type: Mapped[str] = mapped_column(String(100))
    estimated_hours: Mapped[float] = mapped_column(Float)
    workers_required: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class Worker(Base):
    __tablename__ = "workers"
    id: Mapped[int] = mapped_column(primary_key=True)
    worker_code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    skills: Mapped[str] = mapped_column(Text, default="")
    available: Mapped[bool] = mapped_column(Boolean, default=True)
    shift_hours: Mapped[float] = mapped_column(Float, default=8)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class OrderPlanningProfile(Base):
    __tablename__ = "order_planning_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    workers_required: Mapped[int] = mapped_column(Integer, default=1)
    preferred_process: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class MachinePerformanceRecord(Base):
    __tablename__ = "machine_performance_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_code: Mapped[str] = mapped_column(String(80), index=True)
    product: Mapped[str] = mapped_column(String(200), default="*")
    process_code: Mapped[str] = mapped_column(String(100), default="*")
    speed_factor: Mapped[float] = mapped_column(Float, default=1.0)
    sample_count: Mapped[int] = mapped_column(Integer, default=1)
    source_note: Mapped[str] = mapped_column(Text, default="")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class MachineDowntimeEvent(Base):
    __tablename__ = "machine_downtime_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    machine_code: Mapped[str] = mapped_column(String(80), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="Unspecified downtime")
    status: Mapped[str] = mapped_column(String(30), default="Open")

class OperationExecution(Base):
    __tablename__ = "operation_executions"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(80), index=True)
    process_code: Mapped[str] = mapped_column(String(100), default="Production")
    machine_code: Mapped[str] = mapped_column(String(80), index=True)
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_quantity: Mapped[int] = mapped_column(Integer, default=0)
    actual_quantity: Mapped[int] = mapped_column(Integer, default=0)
    downtime_hours: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(30), default="Recorded")
    delay_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

class PlanningRun(Base):
    __tablename__ = "planning_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    solver: Mapped[str] = mapped_column(String(100))
    elapsed_seconds: Mapped[float] = mapped_column(Float)
    order_count: Mapped[int] = mapped_column(Integer)
    assigned_count: Mapped[int] = mapped_column(Integer)
    late_count: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(Text, default="")
