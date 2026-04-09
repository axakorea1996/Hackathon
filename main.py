from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import random, string, os, re, time, html
from datetime import datetime
from collections import defaultdict
 
app = FastAPI()
templates = Jinja2Templates(directory="templates")
 
# ── DB 연결 ───────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL")
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
    user_id    = Column(String, unique=True)   # 동일 사용자 중복 청약 방지
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
 
 
# ── 세션 & 유틸 ──────────────────────────────────────────
sessions = {}
 
def gen_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=64))
 
def gen_receipt():
    return "INS-" + ''.join(random.choices(string.digits, k=8))
 
 
# ── Rate Limiting (메모리 기반) ───────────────────────────
# IP당 로그인 시도 5회 / 60초 초과 시 차단
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
    """XSS 방지: HTML 특수문자 이스케이프"""
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
    # 스크립트 태그 등 이상한 입력 차단
    if re.search(r"[<>{}\[\];]", name):
        return "성명에 특수문자를 사용할 수 없습니다."
    return None
 
 
# ── 보안 헤더 미들웨어 ────────────────────────────────────
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    # XSS 방어
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # HTTPS 강제 (Render는 HTTPS 제공)
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # 불필요한 서버 정보 숨기기
    response.headers["Server"] = "webserver"
    # 콘텐츠 보안 정책
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'"
    return response
 
 
# ── 세션 유효성 체크 헬퍼 ─────────────────────────────────
def get_session_user(request: Request) -> str | None:
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        return None
    return sessions[token]["user_id"]
 
 
# ── 라우트 ────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {})
 
 
@app.post("/login")
async def login(request: Request, user_id: str = Form(...), password: str = Form(...)):
    # Rate Limiting
    client_ip = request.client.host
    if is_rate_limited(client_ip):
        return templates.TemplateResponse(request, "login.html", {
            "error": "로그인 시도가 너무 많습니다. 1분 후 다시 시도해주세요."
        })
 
    # 입력값 새니타이징
    user_id  = sanitize(user_id)
    password = sanitize(password)
 
    # 길이 제한 (지나치게 긴 입력 차단)
    if len(user_id) > 50 or len(password) > 100:
        return templates.TemplateResponse(request, "login.html", {
            "error": "입력값이 올바르지 않습니다."
        })
 
    # DB 조회 (SQLAlchemy ORM → SQL Injection 자동 방어)
    db = SessionLocal()
    user = db.query(User).filter(
        User.user_id == user_id,
        User.password == password
    ).first()
    db.close()
 
    if not user:
        # 실패 메시지를 모호하게 → 어떤 항목이 틀렸는지 노출 안 함
        return templates.TemplateResponse(request, "login.html", {
            "error": "아이디 또는 비밀번호가 올바르지 않습니다."
        })
 
    token = gen_token()
    sessions[token] = {"user_id": user_id}
    response = RedirectResponse(url="/apply", status_code=302)
    # HttpOnly: JS에서 쿠키 접근 차단, SameSite: CSRF 방어
    response.set_cookie(
        key="session_token", value=token,
        httponly=True, samesite="lax", secure=True
    )
    return response
 
 
@app.get("/apply", response_class=HTMLResponse)
async def apply_page(request: Request):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse(url="/")
 
    # 이미 청약한 사용자 확인
    db = SessionLocal()
    existing = db.query(Application).filter(Application.user_id == user_id).first()
    db.close()
 
    if existing:
        return templates.TemplateResponse(request, "apply.html", {
            "user_id": user_id,
            "already_applied": True,
            "receipt": existing.receipt
        })
 
    return templates.TemplateResponse(request, "apply.html", {"user_id": user_id})
 
 
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
 
    # ── 중복 청약 방지 (DB 레벨 재확인) ──
    db = SessionLocal()
    existing = db.query(Application).filter(Application.user_id == user_id).first()
    db.close()
    if existing:
        return RedirectResponse(url=f"/complete?receipt={existing.receipt}", status_code=302)
 
    # ── 허용된 보장 항목만 통과 (파라미터 변조 방지) ──
    if coverage not in ALLOWED_COVERAGES:
        raise HTTPException(status_code=400, detail="올바르지 않은 보장 항목입니다.")
 
    # ── 입력값 새니타이징 ──
    name  = sanitize(name)
    birth = sanitize(birth)
    phone = sanitize(phone)
    email = sanitize(email.lower())
 
    # ── 정합성 검증 ──
    errors = {}
    name_err = validate_name(name)
    if name_err:   errors["name"]  = name_err
    birth_err = validate_birth(birth)
    if birth_err:  errors["birth"] = birth_err
    phone_err = validate_phone(phone)
    if phone_err:  errors["phone"] = phone_err
    email_err = validate_email(email)
    if email_err:  errors["email"] = email_err
 
    if errors:
        return templates.TemplateResponse(request, "apply.html", {
            "user_id": user_id,
            "errors": errors,
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
    except Exception:
        db.rollback()
        # unique 제약 조건 위반 → 이미 청약된 경우
        existing = db.query(Application).filter(Application.user_id == user_id).first()
        db.close()
        if existing:
            return RedirectResponse(url=f"/complete?receipt={existing.receipt}", status_code=302)
        raise HTTPException(status_code=500, detail="청약 처리 중 오류가 발생했습니다.")
    finally:
        db.close()
 
    return RedirectResponse(url=f"/loading?receipt={receipt}", status_code=302)
 
 
@app.get("/loading", response_class=HTMLResponse)
async def loading_page(request: Request, receipt: str):
    if not get_session_user(request):
        return RedirectResponse(url="/")
    # receipt 형식 검증 (파라미터 변조 방지)
    if not re.fullmatch(r"INS-\d{8}", receipt):
        raise HTTPException(status_code=400, detail="올바르지 않은 접수번호입니다.")
    return templates.TemplateResponse(request, "loading.html", {"receipt": receipt})
 
 
@app.get("/complete", response_class=HTMLResponse)
async def complete_page(request: Request, receipt: str):
    user_id = get_session_user(request)
    if not user_id:
        return RedirectResponse(url="/")
    if not re.fullmatch(r"INS-\d{8}", receipt):
        raise HTTPException(status_code=400, detail="올바르지 않은 접수번호입니다.")
 
    db = SessionLocal()
    # 본인 청약 데이터만 조회 (다른 사용자 데이터 접근 차단)
    data = db.query(Application).filter(
        Application.receipt == receipt,
        Application.user_id == user_id
    ).first()
    db.close()
 
    if not data:
        raise HTTPException(status_code=403, detail="접근 권한이 없습니다.")
 
    return templates.TemplateResponse(request, "complete.html", {"data": data})
 
 
@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token in sessions:
        del sessions[token]
    response = RedirectResponse(url="/")
    response.delete_cookie("session_token")
    return response
