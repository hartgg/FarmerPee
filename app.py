from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from passlib.context import CryptContext
from sqlmodel import SQLModel, Field, Session, create_engine, select

APP_NAME = "RaiDaeng FarmOS"

# ✅ รองรับออนไลน์
DB_URL = os.getenv("DATABASE_URL") or os.getenv("FARMOS_DB", "sqlite:///farm.db")

connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args)

pwd_context = CryptContext(schemes=["bcrypt_sha256"], deprecated="auto")


# -----------------------------
# Models
# -----------------------------
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(default="farmer")  # owner / farmer
    full_name: Optional[str] = Field(default="")
    phone: Optional[str] = Field(default="")
    image: Optional[str] = Field(default="")


class Farmer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    code: str = Field(index=True, unique=True)
    name: str = Field(index=True)
    phone: str = Field(index=True)


class Plot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    farmer_id: int = Field(foreign_key="farmer.id", index=True)
    plot_name: str
    area_rai: float = 0.0


class Planting(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plot_id: int = Field(foreign_key="plot.id", index=True)
    plant_date: date
    days_to_harvest: int = 120
    yield_ton_per_rai: float = 1.5
    status: str = "ปลูกแล้ว"

    @property
    def harvest_date(self) -> date:
        return self.plant_date + timedelta(days=self.days_to_harvest)


# -----------------------------
# App
# -----------------------------
app = FastAPI(title=APP_NAME)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def create_db():
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        admin = session.exec(select(User).where(User.username == "admin")).first()
        if not admin:
            session.add(
                User(
                    username="admin",
                    password_hash=pwd_context.hash("admin1234"),
                    role="owner",
                    full_name="Owner"
                )
            )
            session.commit()


create_db()


def get_session():
    with Session(engine) as session:
        yield session


# -----------------------------
# Auth
# -----------------------------
def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    username = request.cookies.get("farmos_user")
    if not username:
        raise PermissionError
    user = session.exec(select(User).where(User.username == username)).first()
    if not user:
        raise PermissionError
    return user


@app.exception_handler(PermissionError)
async def perm_handler(request: Request, exc: PermissionError):
    return RedirectResponse("/login", status_code=303)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register(
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    phone: str = Form(""),
    session: Session = Depends(get_session),
):
    user = User(
        username=username,
        password_hash=pwd_context.hash(password),
        role="farmer",
        full_name=full_name,
        phone=phone,
    )
    session.add(user)
    session.commit()
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        return RedirectResponse("/login?err=1", status_code=303)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("farmos_user", username, httponly=True)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("farmos_user")
    return resp


# -----------------------------
# Profile
# -----------------------------
@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse("profile.html", {"request": request, "user": user})


# -----------------------------
# Dashboard
# -----------------------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)):
    farmers = session.exec(select(Farmer)).all()

    if user.role == "farmer":
        farmers = [f for f in farmers if f.user_id == user.id]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "farmers": farmers,
        },
    )


# -----------------------------
# Plantings (เดือน + รวมตัน)
# -----------------------------
def month_key(d: date):
    return f"{d.year:04d}-{d.month:02d}"


@app.get("/plantings", response_class=HTMLResponse)
def plantings_page(
    request: Request,
    month: str = "",
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    plantings = session.exec(select(Planting)).all()
    plots = session.exec(select(Plot)).all()
    farmers = session.exec(select(Farmer)).all()

    plot_map = {p.id: p for p in plots}
    farmer_map = {f.id: f for f in farmers}

    rows = []
    for pl in plantings:
        plot = plot_map.get(pl.plot_id)
        farmer = farmer_map.get(plot.farmer_id) if plot else None

        if user.role == "farmer":
            if not farmer or farmer.user_id != user.id:
                continue

        tons = (plot.area_rai if plot else 0) * pl.yield_ton_per_rai

        rows.append({
            "pl": pl,
            "farmer": farmer,
            "plot": plot,
            "harvest_month": month_key(pl.harvest_date),
            "expected_tons": round(tons, 3)
        })

    if month:
        rows = [r for r in rows if r["harvest_month"] == month]

    total_tons = round(sum(r["expected_tons"] for r in rows), 3)

    return templates.TemplateResponse(
        "plantings.html",
        {
            "request": request,
            "rows": rows,
            "month": month,
            "total_tons": total_tons,
        },
    )
