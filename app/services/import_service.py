import io, re
import pandas as pd
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy import select
from app.models import Order

ALIASES = {
 "order_id": ["order_id","order id","order number","order no","رقم الطلب","رقم امر الشغل","رقم أمر الشغل","كود الطلب"],
 "product": ["product","product name","product code","المنتج","اسم المنتج","كود المنتج"],
 "quantity": ["quantity","qty","الكمية","كميه"],
 "due_date": ["due_date","due date","delivery date","deadline","تاريخ التسليم","موعد التسليم"],
 "priority": ["priority","الأولوية","الاولويه"],
 "machine_type": ["machine_type","machine type","نوع الماكينة","نوع الماكينه"],
 "customer": ["customer","client","العميل","اسم العميل"],
 "estimated_hours": ["estimated_hours","estimated hours","hours","ساعات تقديرية"],
 "notes": ["notes","ملاحظات"]
}
REQUIRED = ["order_id","product","quantity","due_date"]
def norm(x):
    return re.sub(r"\s+", " ", str(x).strip().lower()).replace("-", "_")

def parse_file(data: bytes, filename: str):
    ext = filename.lower().rsplit(".",1)[-1]
    if ext not in {"xlsx","xls","csv"}: raise ValueError("Supported formats: .xlsx, .xls, .csv")
    try:
        df = pd.read_csv(io.BytesIO(data)) if ext == "csv" else pd.read_excel(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"Could not read file: {e}") from e
    df.columns = [str(c).strip() for c in df.columns]
    mapping = {}
    for col in df.columns:
        key = norm(col)
        for field, aliases in ALIASES.items():
            if key in {norm(a) for a in aliases}:
                mapping[field] = col
    missing = [x for x in REQUIRED if x not in mapping]
    rows, errors = [], []
    if missing: return {"columns": list(df.columns), "mapping": mapping, "missing_columns": missing, "rows": [], "errors": [{"row": 0, "error": "Missing required columns: " + ", ".join(missing)}]}
    seen = set()
    for idx, raw in df.iterrows():
        rowno = int(idx)+2
        try:
            order_id = str(raw[mapping["order_id"]]).strip()
            if not order_id or order_id.lower() == "nan": raise ValueError("order_id is required")
            if order_id.endswith(".0"): order_id = order_id[:-2]
            if order_id in seen: raise ValueError("Duplicate order_id within file")
            seen.add(order_id)
            product = str(raw[mapping["product"]]).strip()
            if not product or product.lower() == "nan": raise ValueError("product is required")
            qv = raw[mapping["quantity"]]
            if pd.isna(qv) or int(qv) != float(qv) or int(qv) <= 0: raise ValueError("quantity must be a positive integer")
            due = pd.to_datetime(raw[mapping["due_date"]], errors="raise").date()
            def opt(field, default=None):
                if field not in mapping or pd.isna(raw[mapping[field]]): return default
                return raw[mapping[field]]
            priority = int(opt("priority",3))
            if not 1 <= priority <= 5: raise ValueError("priority must be 1..5")
            hours = opt("estimated_hours")
            record = {"order_id":order_id,"product":product,"quantity":int(qv),"due_date":due.isoformat(),
                      "priority":priority,"machine_type":str(opt("machine_type","general")),
                      "estimated_hours":float(hours) if hours is not None else None,
                      "customer":str(opt("customer")) if opt("customer") is not None else None,
                      "notes":str(opt("notes")) if opt("notes") is not None else None}
            rows.append({"row":rowno,"valid":True,"data":record,"error":None})
        except Exception as e:
            errors.append({"row":rowno,"error":str(e)})
            rows.append({"row":rowno,"valid":False,"data":None,"error":str(e)})
    return {"columns":list(df.columns),"mapping":mapping,"missing_columns":[],"rows":rows,"errors":errors}

def commit_import(db: Session, parsed: dict):
    inserted, skipped, rejected = [], [], []
    for row in parsed["rows"]:
        if not row["valid"]:
            rejected.append({"row":row["row"],"error":row["error"]}); continue
        data=row["data"]
        if db.scalar(select(Order).where(Order.order_id == data["order_id"])):
            skipped.append({"row":row["row"],"order_id":data["order_id"],"reason":"Order ID already exists"})
            continue
        from datetime import date as dt
        data["due_date"] = dt.fromisoformat(data["due_date"])
        db.add(Order(**data))
        inserted.append(data["order_id"])
    try: db.commit()
    except Exception:
        db.rollback()
        raise
    return {"inserted":len(inserted),"skipped":len(skipped),"rejected":len(rejected),"inserted_order_ids":inserted,"skipped_rows":skipped,"rejected_rows":rejected}
