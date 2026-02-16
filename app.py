from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional, List

from fastapi import FastAPI, Request, Form, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from passlib.context import CryptContext
from sqlmodel import SQLModel, Field, Session, create_engine, select

APP_NAME = "RaiDaeng FarmOS"
DB_URL = os.getenv("FARMOS_DB", "sqlite:///farm.db")

pwd_context = CryptContext(
    schemes=["bcrypt_sha256"],
    deprecated="auto"
)

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})


# -----------------------------
# Models
# -----------------------------
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str


class Farmer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)  # e.g., RD001
    name: str = Field(index=True)
    phone: str = Field(index=True)
    line_id: Optional[str] = Field(default="")
    province: Optional[str] = Field(default="")
    district: Optional[str] = Field(default="")
    address: Optional[str] = Field(default="")
    notes: Optional[str] = Field(default="")


class Plot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    farmer_id: int = Field(foreign_key="farmer.id", index=True)
    plot_name: str = Field(index=True)
    area_rai: float = Field(default=0.0)
    location_hint: Optional[str] = Field(default="")  # free text / lat,lng later


class Planting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plot_id: int = Field(foreign_key="plot.id", index=True)
    crop: str = Field(default="มันหวานญี่ปุ่น")
    variety: str = Field(default="Silk Sweet")
    plant_date: date = Field(index=True)
    days_to_harvest: int = Field(default=120)  # editable per record
    yield_ton_per_rai: float = Field(default=1.5)  # default from your assumption
    status: str = Field(default="ปลูกแล้ว")  # planned / planted / harvested
    notes: Optional[str] = Field(default="")

    @property
    def harvest_date(self) -> date:
        return self.plant_date + timedelta(days=self.days_to_harvest)


# -----------------------------
# App init
# -----------------------------
app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def create_db_and_seed():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        # seed admin if none
        user = session.exec(select(User).where(User.username == "admin")).first()
        if not user:
            session.add(User(username="admin", password_hash=pwd_context.hash("admin1234")))
            session.commit()


create_db_and_seed()


def get_session():
    with Session(engine) as session:
        yield session


# -----------------------------
# Simple cookie auth
# -----------------------------
def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    username = request.cookies.get("farmos_user")
    if not username:
        raise PermissionError("Not logged in")
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise PermissionError("Invalid user")
    return user


@app.exception_handler(PermissionError)
async def perm_handler(request: Request, exc: PermissionError):
    return RedirectResponse("/login", status_code=303)


# -----------------------------
# Helpers
# -----------------------------
def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# -----------------------------
# Auth
# -----------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "app": APP_NAME})


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        return RedirectResponse("/login?err=1", status_code=303)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("farmos_user", username, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("farmos_user")
    return resp


# -----------------------------
# Dashboard
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    # summary cards
    farmers = session.exec(select(Farmer)).all()
    plots = session.exec(select(Plot)).all()
    plantings = session.exec(select(Planting)).all()

    # harvest plan: sum expected yield by harvest month
    # expected yield = plot.area_rai * planting.yield_ton_per_rai
    plot_area = {p.id: p.area_rai for p in plots}
    by_month = {}
    for pl in plantings:
        hk = month_key(pl.harvest_date)
        tons = plot_area.get(pl.plot_id, 0.0) * pl.yield_ton_per_rai
        by_month[hk] = by_month.get(hk, 0.0) + tons

    months_sorted = sorted(by_month.keys())
    series = [{"month": m, "tons": round(by_month[m], 3)} for m in months_sorted]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "app": APP_NAME,
            "user": user.username,
            "kpi": {
                "farmers": len(farmers),
                "plots": len(plots),
                "plantings": len(plantings),
                "next_month_tons": series[0]["tons"] if series else 0.0,
            },
            "series": series,
        },
    )


@app.get("/api/harvest_series")
def harvest_series(user: User = Depends(require_user), session: Session = Depends(get_session)):
    plots = session.exec(select(Plot)).all()
    plantings = session.exec(select(Planting)).all()
    plot_area = {p.id: p.area_rai for p in plots}
    by_month = {}
    for pl in plantings:
        hk = month_key(pl.harvest_date)
        tons = plot_area.get(pl.plot_id, 0.0) * pl.yield_ton_per_rai
        by_month[hk] = by_month.get(hk, 0.0) + tons
    months_sorted = sorted(by_month.keys())
    return [{"month": m, "tons": round(by_month[m], 3)} for m in months_sorted]


# -----------------------------
# Farmers (CRUD + search)
# -----------------------------
@app.get("/farmers", response_class=HTMLResponse)
def farmers_page(
    request: Request,
    q: str = "",
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    stmt = select(Farmer)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            (Farmer.name.like(like))
            | (Farmer.phone.like(like))
            | (Farmer.code.like(like))
            | (Farmer.province.like(like))
        )
    rows = session.exec(stmt.order_by(Farmer.code)).all()
    return templates.TemplateResponse("farmers.html", {"request": request, "app": APP_NAME, "rows": rows, "q": q})


@app.get("/farmers/new", response_class=HTMLResponse)
def farmer_new(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse("farmer_form.html", {"request": request, "app": APP_NAME, "mode": "new"})


@app.post("/farmers/new")
def farmer_create(
    code: str = Form(...),
    name: str = Form(...),
    phone: str = Form(...),
    line_id: str = Form(""),
    province: str = Form(""),
    district: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    session.add(
        Farmer(
            code=code.strip(),
            name=name.strip(),
            phone=phone.strip(),
            line_id=line_id.strip(),
            province=province.strip(),
            district=district.strip(),
            address=address.strip(),
            notes=notes.strip(),
        )
    )
    session.commit()
    return RedirectResponse("/farmers", status_code=303)


@app.get("/farmers/{farmer_id}/edit", response_class=HTMLResponse)
def farmer_edit(farmer_id: int, request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    row = session.get(Farmer, farmer_id)
    return templates.TemplateResponse("farmer_form.html", {"request": request, "app": APP_NAME, "mode": "edit", "row": row})


@app.post("/farmers/{farmer_id}/edit")
def farmer_update(
    farmer_id: int,
    code: str = Form(...),
    name: str = Form(...),
    phone: str = Form(...),
    line_id: str = Form(""),
    province: str = Form(""),
    district: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    row = session.get(Farmer, farmer_id)
    row.code = code.strip()
    row.name = name.strip()
    row.phone = phone.strip()
    row.line_id = line_id.strip()
    row.province = province.strip()
    row.district = district.strip()
    row.address = address.strip()
    row.notes = notes.strip()
    session.add(row)
    session.commit()
    return RedirectResponse("/farmers", status_code=303)


@app.post("/farmers/{farmer_id}/delete")
def farmer_delete(farmer_id: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    row = session.get(Farmer, farmer_id)
    if row:
        session.delete(row)
        session.commit()
    return RedirectResponse("/farmers", status_code=303)


# -----------------------------
# Plots
# -----------------------------
@app.get("/plots", response_class=HTMLResponse)
def plots_page(
    request: Request,
    q: str = "",
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    farmers = session.exec(select(Farmer)).all()
    farmer_map = {f.id: f for f in farmers}
    stmt = select(Plot)
    rows = session.exec(stmt.order_by(Plot.id.desc())).all()

    # apply search on joined fields manually
    if q.strip():
        like = q.strip().lower()
        rows = [
            p for p in rows
            if like in (p.plot_name or "").lower()
            or like in (farmer_map.get(p.farmer_id).name or "").lower()
            or like in (farmer_map.get(p.farmer_id).code or "").lower()
        ]

    return templates.TemplateResponse(
        "plots.html",
        {"request": request, "app": APP_NAME, "rows": rows, "farmers": farmer_map, "q": q},
    )


@app.get("/plots/new", response_class=HTMLResponse)
def plot_new(request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    farmers = session.exec(select(Farmer).order_by(Farmer.code)).all()
    return templates.TemplateResponse("plot_form.html", {"request": request, "app": APP_NAME, "mode": "new", "farmers": farmers})


@app.post("/plots/new")
def plot_create(
    farmer_id: int = Form(...),
    plot_name: str = Form(...),
    area_rai: float = Form(0.0),
    location_hint: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    session.add(Plot(farmer_id=farmer_id, plot_name=plot_name.strip(), area_rai=float(area_rai), location_hint=location_hint.strip()))
    session.commit()
    return RedirectResponse("/plots", status_code=303)


@app.get("/plots/{plot_id}/edit", response_class=HTMLResponse)
def plot_edit(plot_id: int, request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    row = session.get(Plot, plot_id)
    farmers = session.exec(select(Farmer).order_by(Farmer.code)).all()
    return templates.TemplateResponse("plot_form.html", {"request": request, "app": APP_NAME, "mode": "edit", "row": row, "farmers": farmers})


@app.post("/plots/{plot_id}/edit")
def plot_update(
    plot_id: int,
    farmer_id: int = Form(...),
    plot_name: str = Form(...),
    area_rai: float = Form(0.0),
    location_hint: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    row = session.get(Plot, plot_id)
    row.farmer_id = farmer_id
    row.plot_name = plot_name.strip()
    row.area_rai = float(area_rai)
    row.location_hint = location_hint.strip()
    session.add(row)
    session.commit()
    return RedirectResponse("/plots", status_code=303)


@app.post("/plots/{plot_id}/delete")
def plot_delete(plot_id: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    row = session.get(Plot, plot_id)
    if row:
        session.delete(row)
        session.commit()
    return RedirectResponse("/plots", status_code=303)


# -----------------------------
# Plantings (Plan / Harvest forecast)
# -----------------------------
from datetime import date
from typing import Optional

@app.get("/plantings", response_class=HTMLResponse)
def plantings_page(
    request: Request,
    q: str = "",
    month: str = "",   # 👈 เพิ่มตัวนี้ (รูปแบบ YYYY-MM)
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):


    farmers = session.exec(select(Farmer)).all()
    plots = session.exec(select(Plot)).all()
    plantings = session.exec(select(Planting).order_by(Planting.plant_date.desc())).all()

    farmer_map = {f.id: f for f in farmers}
    plot_map = {p.id: p for p in plots}

    rows = []
    for pl in plantings:
        p = plot_map.get(pl.plot_id)
        f = farmer_map.get(p.farmer_id) if p else None
        tons = (p.area_rai if p else 0.0) * pl.yield_ton_per_rai
        rows.append(
            {
                "pl": pl,
                "plot": p,
                "farmer": f,
                "harvest_date": pl.harvest_date,
                "harvest_month": month_key(pl.harvest_date),
                "expected_tons": round(tons, 3),
            }
        )

    if q.strip():
        like = q.strip().lower()
        rows = [
            r for r in rows
            if like in (r["farmer"].name if r["farmer"] else "").lower()
            or like in (r["farmer"].code if r["farmer"] else "").lower()
            or like in (r["plot"].plot_name if r["plot"] else "").lower()
            or like in (r["pl"].variety or "").lower()
            or like in (r["pl"].status or "").lower()
        ]
    
    # filter by month (YYYY-MM)
    if month:
        rows = [
            r for r in rows
            if r["harvest_month"] == month
    ]

    total_tons = round(sum(r["expected_tons"] for r in rows), 3)

    
    return templates.TemplateResponse(
    "plantings.html",
    {
        "request": request,
        "app": APP_NAME,
        "rows": rows,
        "q": q,
        "month": month,
        "total_tons": total_tons,
    },
    )



@app.get("/plantings/new", response_class=HTMLResponse)
def planting_new(request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    plots = session.exec(select(Plot).order_by(Plot.id.desc())).all()
    farmers = session.exec(select(Farmer)).all()
    farmer_map = {f.id: f for f in farmers}
    return templates.TemplateResponse("planting_form.html", {"request": request, "app": APP_NAME, "mode": "new", "plots": plots, "farmers": farmer_map})


@app.post("/plantings/new")
def planting_create(
    plot_id: int = Form(...),
    crop: str = Form("มันหวานญี่ปุ่น"),
    variety: str = Form("Silk Sweet"),
    plant_date: str = Form(...),  # YYYY-MM-DD
    days_to_harvest: int = Form(120),
    yield_ton_per_rai: float = Form(1.5),
    status: str = Form("ปลูกแล้ว"),
    notes: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    y, m, d = [int(x) for x in plant_date.split("-")]
    session.add(
        Planting(
            plot_id=plot_id,
            crop=crop.strip(),
            variety=variety.strip(),
            plant_date=date(y, m, d),
            days_to_harvest=int(days_to_harvest),
            yield_ton_per_rai=float(yield_ton_per_rai),
            status=status.strip(),
            notes=notes.strip(),
        )
    )
    session.commit()
    return RedirectResponse("/plantings", status_code=303)


@app.get("/plantings/{planting_id}/edit", response_class=HTMLResponse)
def planting_edit(planting_id: int, request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    row = session.get(Planting, planting_id)
    plots = session.exec(select(Plot).order_by(Plot.id.desc())).all()
    farmers = session.exec(select(Farmer)).all()
    farmer_map = {f.id: f for f in farmers}
    return templates.TemplateResponse("planting_form.html", {"request": request, "app": APP_NAME, "mode": "edit", "row": row, "plots": plots, "farmers": farmer_map})


@app.post("/plantings/{planting_id}/edit")
def planting_update(
    planting_id: int,
    plot_id: int = Form(...),
    crop: str = Form("มันหวานญี่ปุ่น"),
    variety: str = Form("Silk Sweet"),
    plant_date: str = Form(...),
    days_to_harvest: int = Form(120),
    yield_ton_per_rai: float = Form(1.5),
    status: str = Form("ปลูกแล้ว"),
    notes: str = Form(""),
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    row = session.get(Planting, planting_id)
    y, m, d = [int(x) for x in plant_date.split("-")]
    row.plot_id = plot_id
    row.crop = crop.strip()
    row.variety = variety.strip()
    row.plant_date = date(y, m, d)
    row.days_to_harvest = int(days_to_harvest)
    row.yield_ton_per_rai = float(yield_ton_per_rai)
    row.status = status.strip()
    row.notes = notes.strip()
    session.add(row)
    session.commit()
    return RedirectResponse("/plantings", status_code=303)


@app.post("/plantings/{planting_id}/delete")
def planting_delete(planting_id: int, user: User = Depends(require_user), session: Session = Depends(get_session)):
    row = session.get(Planting, planting_id)
    if row:
        session.delete(row)
        session.commit()
    return RedirectResponse("/plantings", status_code=303)


# -----------------------------
# Export CSV (harvest plan)
# -----------------------------
@app.get("/export/harvest.csv")
def export_harvest_csv(user: User = Depends(require_user), session: Session = Depends(get_session)):
    farmers = session.exec(select(Farmer)).all()
    plots = session.exec(select(Plot)).all()
    plantings = session.exec(select(Planting)).all()

    farmer_map = {f.id: f for f in farmers}
    plot_map = {p.id: p for p in plots}

    lines = ["month,farmer_code,farmer_name,plot_name,area_rai,variety,plant_date,harvest_date,expected_tons,status"]
    for pl in plantings:
        p = plot_map.get(pl.plot_id)
        f = farmer_map.get(p.farmer_id) if p else None
        tons = (p.area_rai if p else 0.0) * pl.yield_ton_per_rai
        lines.append(
            f"{month_key(pl.harvest_date)},"
            f"{(f.code if f else '')},"
            f"{(f.name if f else '')},"
            f"{(p.plot_name if p else '')},"
            f"{(p.area_rai if p else 0)},"
            f"{pl.variety},"
            f"{pl.plant_date.isoformat()},"
            f"{pl.harvest_date.isoformat()},"
            f"{round(tons,3)},"
            f"{pl.status}"
        )

    csv_text = "\n".join(lines)
    return JSONResponse(content={"filename": "harvest.csv", "csv": csv_text})
