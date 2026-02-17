from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, Request, Form, Depends, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from passlib.context import CryptContext
from sqlmodel import SQLModel, Field, Session, create_engine, select


APP_NAME = "RaiDaeng FarmOS"

# -----------------------------
# DATABASE
# -----------------------------
DB_URL = os.getenv("DATABASE_URL") or os.getenv("FARMOS_DB", "sqlite:///farm.db")
connect_args = {"check_same_thread": False} if DB_URL.startswith("sqlite") else {}
engine = create_engine(DB_URL, connect_args=connect_args)

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# -----------------------------
# UPLOAD FOLDER
# -----------------------------
UPLOAD_DIR = "static/uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# -----------------------------
# MODELS
# -----------------------------
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(default="farmer")
    full_name: str
    phone: str
    image: Optional[str] = None   # ✅ เพิ่มรูปโปรไฟล์


class Farmer(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    code: str = Field(index=True, unique=True)
    name: str
    phone: str


class Plot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    farmer_id: int = Field(foreign_key="farmer.id", index=True)
    plot_name: str
    area_rai: float


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
# APP
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
                    full_name="Owner",
                    phone="0000000000",
                )
            )
            session.commit()


create_db()


def get_session():
    with Session(engine) as session:
        yield session


# -----------------------------
# AUTH
# -----------------------------
def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    user_id = request.cookies.get("farmos_user")
    if not user_id:
        raise PermissionError
    user = session.get(User, int(user_id))
    if not user:
        raise PermissionError
    return user


@app.exception_handler(PermissionError)
async def perm_handler(request: Request, exc: PermissionError):
    return RedirectResponse("/login", status_code=303)


# -----------------------------
# REGISTER (รองรับรูป)
# -----------------------------
@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})


@app.post("/register")
def register(
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    phone: str = Form(...),
    image: UploadFile = File(None),   # ✅ รับรูป
    session: Session = Depends(get_session),
):
    # เช็ค username ซ้ำ
    exists = session.exec(select(User).where(User.username == username)).first()
    if exists:
        return RedirectResponse("/register?err=1", status_code=303)

    filename = None
    if image:
        filename = image.filename
        filepath = os.path.join(UPLOAD_DIR, filename)
        with open(filepath, "wb") as buffer:
            buffer.write(image.file.read())

    user = User(
        username=username,
        password_hash=pwd_context.hash(password),
        role="farmer",
        full_name=full_name,
        phone=phone,
        image=filename,  # ✅ บันทึกรูป
    )
    session.add(user)
    session.commit()
    session.refresh(user)

    farmer = Farmer(
        user_id=user.id,
        code=f"F{user.id:04d}",
        name=full_name,
        phone=phone,
    )
    session.add(farmer)
    session.commit()

    return RedirectResponse("/login", status_code=303)


# -----------------------------
# LOGIN
# -----------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not pwd_context.verify(password, user.password_hash):
        return RedirectResponse("/login?err=1", status_code=303)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("farmos_user", str(user.id), httponly=True)
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("farmos_user")
    return resp


# -----------------------------
# PROFILE PAGE
# -----------------------------
@app.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, user: User = Depends(require_user)):
    return templates.TemplateResponse(
        "profile.html",
        {"request": request, "user": user},
    )
