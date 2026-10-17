from datetime import date
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal

Statuses = Literal["Pending","Planned","Approved","In Production","Completed","Delayed","Cancelled"]

class OrderIn(BaseModel):
    order_id: str = Field(min_length=1, max_length=80)
    product: str = Field(min_length=1, max_length=200)
    quantity: int = Field(gt=0)
    due_date: date
    order_date: date | None = None
    priority: int = Field(default=3, ge=1, le=5)
    machine_type: str = Field(default="general", min_length=1, max_length=100)
    estimated_hours: float | None = Field(default=None, gt=0)
    customer: str | None = None
    notes: str | None = None
    status: Statuses = "Pending"

class OrderUpdate(BaseModel):
    product: str | None = None
    quantity: int | None = Field(default=None, gt=0)
    due_date: date | None = None
    order_date: date | None = None
    priority: int | None = Field(default=None, ge=1, le=5)
    machine_type: str | None = None
    estimated_hours: float | None = Field(default=None, gt=0)
    customer: str | None = None
    notes: str | None = None
    status: Statuses | None = None

class MachineIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    machine_type: str = Field(min_length=1, max_length=100)
    capacity_per_day: float = Field(gt=0)
    available: bool = True
    shift_hours: float = Field(default=8, gt=0, le=24)
    maintenance_start: date | None = None
    maintenance_end: date | None = None
    notes: str | None = None

class ActualIn(BaseModel):
    order_id: str
    production_date: date
    machine_code: str
    planned_quantity: int = Field(ge=0)
    actual_quantity: int = Field(ge=0)
    downtime_hours: float | None = Field(default=None, ge=0)
    delay_reason: str | None = None
    notes: str | None = None


class MaterialIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=200)
    unit: str = Field(default="kg", min_length=1, max_length=30)
    on_hand: float = Field(default=0, ge=0)
    reorder_point: float = Field(default=0, ge=0)
    supplier: str | None = None
    notes: str | None = None

class MaterialUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    unit: str | None = Field(default=None, min_length=1, max_length=30)
    on_hand: float | None = Field(default=None, ge=0)
    reorder_point: float | None = Field(default=None, ge=0)
    supplier: str | None = None
    notes: str | None = None

class BOMIn(BaseModel):
    product: str = Field(min_length=1, max_length=200)
    material_code: str = Field(min_length=1, max_length=80)
    quantity_per_unit: float = Field(gt=0)
