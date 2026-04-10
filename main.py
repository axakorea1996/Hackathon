from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, String, DateTime, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import random, string, os, re, time, html, secrets, logging, traceback
from datetime import datetime
from collections import defaultdict

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ── DB 연결 ───────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./test.db")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


# ── 테이블 정의 ───────────────────────────────────────────
class User(Base):
    __tablename__ = "users"
    user_id  = Column(String, primary_key=True)
    password = Column(String)

class Application(Base):
    __tablename__ = "applications"
    receipt    = Column(String, primary_key=True)
    user_id    = Column(String)           # unique 제거 → 한 계정으로 여러 청약 가능
    name       = Column(String)
    birth      = Column(String)
    phone      = Column(String)
    email      = Column(String)
    coverage   = Column(String)
    created_at = Column(DateTime, default=datetime.now)

Base.metadata.create_all(bind=engine)


# ── 초기 계정 생성 ────────────────────────────────────────
def init_users():
    db = SessionLocal()
    if not db.query(User).first():
        db.add(User(user_id="admin", password="1234"))
        db.add(User(user_id="test",  password="test123"))
        db.commit()
    db.close()

init_users()

# ── DB 마이그레이션 (최초 1회 실행) ──────────────────────
def migrate():
    try:
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_user_id_key"))
            conn.commit()
            logger.info("마이그레이션 완료: applications_user_id_key 제거")
    except Exception as e:
        logger.warning("마이그레이션 건너뜀: %s", str(e))

migrate()

sessions = {}

def gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=64))

def gen_receipt():
    return "INS-" + ''.join(random.choices(string.digits, k=8))


# ── Rate Limiting ─────────────────────────────────────────
login_attempts: dict = defaultdict(list)

def is_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in login_attempts[ip] if now - t < 60]
    login_attempts[ip] = attempts
    if len(attempts) >= 5:
        return True
    login_attempts[ip].append(now)
    return False


# ── 입력값 새니타이징 ─────────────────────────────────────
def sanitize(value: str) -> str:
    return html.escape(value.strip()) if value else ""

ALLOWED_COVERAGES = {
    "기본형 (월 12,000원)",
    "표준형 (월 20,500원)",
    "종합형 (월 31,200원)",
    "실속형 (월 8,500원)"
}


# ── 정합성 검증 ───────────────────────────────────────────
def validate_phone(phone: str) -> str | None:
    clean = re.sub(r"[-\s]", "", phone)
    if not re.fullmatch(r"01[016789]\d{7,8}", clean):
        return "휴대폰 번호 형식이 올바르지 않습니다. (예: 010-1234-5678)"
    return None

def validate_email(email: str) -> str | None:
    if not re.fullmatch(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", email):
        return "이메일 형식이 올바르지 않습니다. (예: example@email.com)"
    return None

def validate_birth(birth: str) -> str | None:
    clean = re.sub(r"[-.\s]", "", birth)
    if not re.fullmatch(r"\d{8}", clean):
        return "생년월일은 8자리 숫자로 입력해주세요. (예: 19900101)"
    try:
        date = datetime.strptime(clean, "%Y%m%d")
        if date > datetime.now():
            return "생년월일이 미래 날짜입니다."
        if date.year < 1900:
            return "생년월일을 다시 확인해주세요."
    except ValueError:
        return "존재하지 않는 날짜입니다. (예: 19900101)"
    return None

def validate_name(name: str) -> str | None:
    if not name.strip():
        return "성명을 입력해주세요."
    if len(name.strip()) < 2:
        return "성명은 2자 이상 입력해주세요."
    if re.search(r"[<>{}\[\];]", name):
        return "성명에 특수문자를 사용할 수 없습니다."
    return None


# ── 보안 헤더 미들웨어 (nonce CSP) ───────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    nonce = secrets.token_urlsafe(16)
    request.state.csp_nonce = nonce
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Server"] = "webserver"
    response.headers["Content-Security-Policy"] = (
        f"default-src 'self'; "
        f"style-src 'self' 'unsafe-inline'; "
        f"script-src 'self' 'nonce-{nonce}'"
    )
    return response

def render(request: Request, template: str, context: dict = {}):
    context["csp_nonce"] = request.state.csp_nonce
    return templates.TemplateResponse(request, template, context)

def get_session_user(request: Request) -> str | None:
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        return None
    return sessions[token]["user_id"]


# ── 라우트: 로그인 ────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return render(request, "login.html")


@app.post("/login")
async def login(request: Request, user_id: str = Form(...), password: str = Form(...)):
    client_ip = request.client.host
    if is_rate_limited(client_ip):
        return render(request, "login.html", {
            "error": "로그인 시도가 너무 많습니다. 1분 후 다시 시도해주세요."
        })

    user_id  = sanitize(user_id)
    password = sanitize(password)

    if len(user_id) > 50 or len(password) > 100:
        return render(request, "login.html", {"error": "입력값이 올바르지 않습니다."})

    db = SessionLocal()
    user = db.query(User).filter(
        User.user_id == user_id,
        User.password == password
    ).first()
    db.close()

    if not user:
        return render(request, "login.html", {
            "error": "아이디 또는 비밀번호가 올바르지 않습니다."
        })

    token = gen_token()
    sessions[token] = {"user_id": user_id}
    response = RedirectResponse(url="/apply", status_code=302)
    response.set_cookie(
        key="session_token", value=token,
        httponly=True, samesite="lax", secure=True
    )
    return response


# ── 라우트: 회원가입 ──────────────────────────────────────
@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return render(request, "register.html")


@app.post("/register")
async def register(
    request: Request,
    user_id:   str = Form(...),
    password:  str = Form(...),
    password2: str = Form(...)
):
    user_id   = sanitize(user_id)
    password  = sanitize(password)
    password2 = sanitize(password2)

    errors = {}

    if not re.fullmatch(r"[a-zA-Z0-9_]{4,20}", user_id):
        errors["user_id"] = "아이디는 영문, 숫자, _만 사용 가능하며 4~20자여야 합니다."
    if len(password) < 6:
        errors["password"] = "비밀번호는 6자 이상이어야 합니다."
    if password != password2:
        errors["password2"] = "비밀번호가 일치하지 않습니다."

    if errors:
        return render(request, "register.html", {"errors": errors, "user_id": user_id})

    db = SessionLocal()
    existing = db.query(User).filter(User.user_id == user_id).first()
    if existing:
        db.close()
        return render(request, "register.html", {
            "errors": {"user_id": "이미 사용 중인 아이디입니다."},
            "user_id": user_id
        })

    db.add(User(user_id=user_id, password=password))
    db.commit()
    db.close()

    return render(request, "login.html", {
        "success": f"회원가입이 완료되었습니다. {user_id}로 로그인해주세요."
    })


# ── 라우트: 청약 ──────────────────────────────────────────
@app.get("/apply", response_class=HTMLResponse)
async def apply_page(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse(url="/")
    return render(request, "apply.html", {"user_id": user_id})


@app.post("/apply")
async def apply_submit(
    request: Request,
    name:     str = Form(...),
    birth:    str = Form(...),
    phone:    str = Form(...),
    email:    str = Form(...),
    coverage: str = Form(...),
    agree:    str = Form(...)
):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse(url="/")

    if coverage not in ALLOWED_COVERAGES:
        raise HTTPException(status_code=400, detail="올바르지 않은 보장 항목입니다.")

    name  = sanitize(name)
    birth = sanitize(re.sub(r"[-.\s]", "", birth))  # 생년월일 하이픈 제거 후 저장
    phone = sanitize(phone)
    email = sanitize(email.lower())

    # ── 정합성 검증 ──
    errors = {}
    name_err = validate_name(name)
    if name_err:  errors["name"]  = name_err
    birth_err = validate_birth(birth)
    if birth_err: errors["birth"] = birth_err
    phone_err = validate_phone(phone)
    if phone_err: errors["phone"] = phone_err
    email_err = validate_email(email)
    if email_err: errors["email"] = email_err

    if errors:
        return render(request, "apply.html", {
            "user_id": user_id,
            "errors": errors,
            "values": {"name": name, "birth": birth, "phone": phone, "email": email, "coverage": coverage}
        })

    # ── 동일 고객 중복 청약 방지 (성명 + 생년월일 기준) ──
    db = SessionLocal()
    duplicate = db.query(Application).filter(
        Application.name  == name,
        Application.birth == birth
    ).first()
    db.close()

    if duplicate:
        return render(request, "apply.html", {
            "user_id": user_id,
            "errors": {
                "name": "동일한 성명과 생년월일로 이미 청약이 완료된 고객입니다.",
                "birth": " "   # 생년월일 필드에도 빨간 테두리 표시
            },
            "values": {"name": name, "birth": birth, "phone": phone, "email": email, "coverage": coverage}
        })

    # ── DB 저장 ──
    receipt = gen_receipt()
    db = SessionLocal()
    try:
        db.add(Application(
            receipt=receipt, user_id=user_id,
            name=name, birth=birth,
            phone=phone, email=email, coverage=coverage
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error("DB 저장 오류: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"청약 처리 오류: {str(e)}")
    finally:
        db.close()

    return RedirectResponse(url=f"/loading?receipt={receipt}", status_code=302)


@app.get("/loading", response_class=HTMLResponse)
async def loading_page(request: Request, receipt: str):
    if not get_session_user(request):
        return RedirectResponse(url="/")
    if not re.fullmatch(r"INS-\d{8}", receipt):
        raise HTTPException(status_code=400, detail="올바르지 않은 접수번호입니다.")
    return render(request, "loading.html", {"receipt": receipt})


@app.get("/complete", response_class=HTMLResponse)
async def complete_page(request: Request, receipt: str):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse(url="/")
    if not re.fullmatch(r"INS-\d{8}", receipt):
        raise HTTPException(status_code=400, detail="올바르지 않은 접수번호입니다.")

    db = SessionLocal()
    data = db.query(Application).filter(
        Application.receipt == receipt,
        Application.user_id == user_id
    ).first()
    db.close()

    if not data:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")

    return render(request, "complete.html", {"data": data})



# ── 라우트: 관리자 청약 목록 ──────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse(url="/")
    if user_id != "admin":
        raise HTTPException(status_code=403, detail="관리자만 접근 가능합니다.")

    db = SessionLocal()
    applications = db.query(Application).order_by(Application.created_at.desc()).all()
    db.close()

    return render(request, "admin.html", {
        "applications": applications,
        "total": len(applications)
    })

@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token in sessions:
        del sessions[token]
    response = RedirectResponse(url="/")
    response.delete_cookie("session_token")
    return response
