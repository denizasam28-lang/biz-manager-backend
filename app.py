# Business Manager (MVP start) — single file
from datetime import datetime, timedelta
from typing import Optional, List, Literal, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Session, select, create_engine

# -------------------- DB MODELS --------------------
class Employee(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: Optional[str] = None
    employment_type: Literal["TFN", "ABN", "INT_STUDENT"] = "TFN"
    tfn: Optional[str] = None
    abn: Optional[str] = None
    hourly_rate: float = 0.0
    role: Optional[str] = None
    max_hours_week: Optional[float] = None
    pay_preference: Literal["bank", "cash"] = "bank"

class Shift(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    employee_id: Optional[int] = None     # can be empty before assignment
    day: str                               # "YYYY-MM-DD"
    start: str                             # "HH:MM"
    end: str                               # "HH:MM"
    role: Optional[str] = None
    expected_busyness: Literal["low","med","high"] = "med"
    max_shift_hours: Optional[float] = None

class Transaction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    date: str
    type: Literal["income","expense"]
    method: Literal["bank","cash"] = "bank"
    category: Optional[str] = None
    amount: float

class TaxSuperRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    super_rate: float = 0.115
    int_student_weekly_cap: float = 24.0
    bracket1_max: float = 500.0
    bracket1_rate: float = 0.05
    bracket2_max: float = 1000.0
    bracket2_base: float = 25.0
    bracket2_rate: float = 0.15
    bracket3_base: float = 100.0
    bracket3_rate: float = 0.25
    abn_withholding_rate: float = 0.0

# -------------------- APP INIT --------------------
engine = create_engine("sqlite:///app.db")
SQLModel.metadata.create_all(engine)
with Session(engine) as s:
    if not s.exec(select(TaxSuperRule)).first():
        s.add(TaxSuperRule()); s.commit()

app = FastAPI(title="Business Manager AI (MVP)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# -------------------- HELPERS --------------------
def parse_time(hhmm: str): return datetime.strptime(hhmm, "%H:%M")
def hours_between(start: str, end: str) -> float:
    s, e = parse_time(start), parse_time(end)
    if e < s: e += timedelta(days=1)
    return (e - s).seconds / 3600
def within_period(day_str: str, start: str, end: str) -> bool:
    d = datetime.strptime(day_str, "%Y-%m-%d")
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    return s <= d <= e

def rules(session: Session) -> TaxSuperRule:
    r = session.exec(select(TaxSuperRule)).first()
    assert r is not None
    return r

def compute_tax(emp: Employee, gross: float, weekly_hours: float, r: TaxSuperRule) -> float:
    if emp.employment_type == "ABN":
        return round(gross * r.abn_withholding_rate, 2)
    if gross <= r.bracket1_max:
        return round(gross * r.bracket1_rate, 2)
    if gross <= r.bracket2_max:
        return round(r.bracket2_base + (gross - r.bracket1_max) * r.bracket2_rate, 2)
    return round(r.bracket3_base + (gross - r.bracket2_max) * r.bracket3_rate, 2)

def compute_super(gross: float, r: TaxSuperRule) -> float:
    return round(gross * r.super_rate, 2)

# -------------------- SCHEMAS --------------------
class EmployeeIn(BaseModel):
    name: str
    email: Optional[str] = None
    employment_type: Literal["TFN","ABN","INT_STUDENT"] = "TFN"
    tfn: Optional[str] = None
    abn: Optional[str] = None
    hourly_rate: float = 0.0
    role: Optional[str] = None
    max_hours_week: Optional[float] = None
    pay_preference: Literal["bank","cash"] = "bank"

class ShiftIn(BaseModel):
    employee_id: Optional[int] = None
    day: str
    start: str
    end: str
    role: Optional[str] = None
    expected_busyness: Literal["low","med","high"] = "med"
    max_shift_hours: Optional[float] = None

class PayrollReq(BaseModel):
    period_start: str
    period_end: str
    default_pay_method: Literal["bank","cash"] = "bank"

class RuleUpdate(BaseModel):
    super_rate: Optional[float] = None
    int_student_weekly_cap: Optional[float] = None
    bracket1_max: Optional[float] = None
    bracket1_rate: Optional[float] = None
    bracket2_max: Optional[float] = None
    bracket2_base: Optional[float] = None
    bracket2_rate: Optional[float] = None
    bracket3_base: Optional[float] = None
    bracket3_rate: Optional[float] = None
    abn_withholding_rate: Optional[float] = None

# -------------------- ROOT + DASHBOARD --------------------
@app.get("/")
def root(): return {"ok": True, "service": "Business Manager API"}
@app.get("/dashboard")
def dashboard():
    with Session(engine) as s:
        employees = s.exec(select(Employee)).all()
        tx = s.exec(select(Transaction)).all()
        income = sum(t.amount for t in tx if t.type=="income")
        expense = sum(t.amount for t in tx if t.type=="expense")
        bank = sum(t.amount for t in tx if t.method=="bank")
        cash = sum(t.amount for t in tx if t.method=="cash")
        return {
            "employees": len(employees),
            "income": round(income,2),
            "expense": round(expense,2),
            "profit_est": round(income-expense,2),
            "cash": round(cash,2),
            "bank": round(bank,2),
        }

# -------------------- EMPLOYEES --------------------
@app.post("/employees")
def create_employee(e: EmployeeIn):
    with Session(engine) as s:
        emp = Employee(**e.model_dump())
        if emp.employment_type == "INT_STUDENT":
            emp.pay_preference = "bank"
        s.add(emp); s.commit(); s.refresh(emp)
        return emp

@app.get("/employees", response_model=List[Employee])
def list_employees():
    with Session(engine) as s:
        return s.exec(select(Employee)).all()

# -------------------- ROSTER --------------------
@app.post("/roster/shifts")
def add_shift(shift: ShiftIn):
    with Session(engine) as s:
        sh = Shift(**shift.model_dump())
        if sh.max_shift_hours is not None:
            h = hours_between(sh.start, sh.end)
            if h > sh.max_shift_hours:
                raise HTTPException(status_code=400, detail="Shift exceeds max_shift_hours")
        s.add(sh); s.commit(); s.refresh(sh)
        return sh

@app.get("/roster/week")
def get_week(start: str, end: Optional[str] = None):
    with Session(engine) as s:
        shifts = s.exec(select(Shift)).all()
        if not end: return shifts
        return [sh for sh in shifts if within_period(sh.day, start, end)]

@app.post("/roster/generate")
def generate_roster():
    # simple: assign cheapest suitable employee by role
    with Session(engine) as s:
        employees = s.exec(select(Employee)).all()
        shifts = s.exec(select(Shift)).all()
        for sh in shifts:
            if sh.employee_id is not None: continue
            cand = [e for e in employees if (sh.role is None or e.role == sh.role)]
            if not cand: continue
            chosen = sorted(cand, key=lambda e: e.hourly_rate)[0]
            sh.employee_id = chosen.id
            s.add(sh)
        s.commit()
        # totals snapshot
        hours_map: Dict[int, float] = {}
        for sh in shifts:
            if not sh.employee_id: continue
            hours_map[sh.employee_id] = hours_map.get(sh.employee_id, 0.0) + hours_between(sh.start, sh.end)
        r = rules(s)
        totals = {"employee_cost": 0.0, "tax_cost": 0.0, "super_cost": 0.0, "cash_cost": 0.0}
        for emp_id, hrs in hours_map.items():
            emp = s.get(Employee, emp_id)
            gross = hrs * emp.hourly_rate
            tax = compute_tax(emp, gross, weekly_hours=hrs, r=r)
            sup = compute_super(gross, r)
            totals["employee_cost"] += gross
            totals["tax_cost"] += tax
            totals["super_cost"] += sup
            if emp.pay_preference == "cash":
                totals["cash_cost"] += gross
        for k in totals: totals[k] = round(totals[k], 2)
        return {"status":"ok", "totals": totals}

# -------------------- PAYROLL --------------------
@app.post("/payroll/calc")
def payroll_calc(req: PayrollReq):
    with Session(engine) as s:
        r = rules(s)
        shifts = s.exec(select(Shift)).all()
        employees = {e.id: e for e in s.exec(select(Employee)).all()}
        per_emp_hours: Dict[int, float] = {}
        for sh in shifts:
            if not sh.employee_id: continue
            if within_period(sh.day, req.period_start, req.period_end):
                per_emp_hours[sh.employee_id] = per_emp_hours.get(sh.employee_id, 0.0) + hours_between(sh.start, sh.end)
        lines = []
        for emp_id, hrs in per_emp_hours.items():
            emp = employees[emp_id]
            gross = round(hrs * float(emp.hourly_rate), 2)
            tax = compute_tax(emp, gross, weekly_hours=hrs, r=r)
            sup = compute_super(gross, r)
            pay_method = emp.pay_preference if emp.employment_type != "ABN" else req.default_pay_method
            lines.append({
                "employee_id": emp_id, "hours": hrs, "gross": gross,
                "tax": tax, "super": sup, "net": round(gross - tax, 2),
                "pay_method": pay_method
            })
        return lines

# -------------------- TAX & SUPER --------------------
@app.get("/taxsuper/rules")
def get_tax_super_rules():
    with Session(engine) as s: return rules(s)

@app.post("/taxsuper/rules")
def update_tax_super_rules(update: RuleUpdate):
    with Session(engine) as s:
        r = rules(s)
        for field, value in update.model_dump(exclude_none=True).items():
            setattr(r, field, value)
        s.add(r); s.commit(); s.refresh(r)
        return r

# -------------------- CASHFLOW --------------------
@app.post("/cashflow/tx")
def add_tx(tx: Transaction):
    with Session(engine) as s:
        s.add(tx); s.commit(); s.refresh(tx); return tx

@app.get("/cashflow/summary")
def cashflow_summary():
    with Session(engine) as s:
        tx = s.exec(select(Transaction)).all()
        income = sum(t.amount for t in tx if t.type=="income")
        expense = sum(t.amount for t in tx if t.type=="expense")
        bank = sum(t.amount for t in tx if t.method=="bank")
        cash = sum(t.amount for t in tx if t.method=="cash")
        wages = sum(t.amount for t in tx if (t.category or "").lower()=="wages")
        ratio = (wages/income*100) if income>0 else 0
        return {
            "income": round(income,2), "expense": round(expense,2),
            "profit_est": round(income-expense,2), "cash": round(cash,2),
            "bank": round(bank,2), "wages_pct_of_income": round(ratio,2)
        }
