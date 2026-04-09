from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import time
import random
import string
 
app = FastAPI()
templates = Jinja2Templates(directory="templates")
 
# 간단한 사용자 데이터 (실제 서비스에서는 DB 사용)
USERS = {
    "admin": "1234",
    "test": "test123"
}
 
# 로그인 세션 (메모리 저장 - 실제 서비스에서는 Redis 등 사용)
sessions = {}
 
# 청약 접수 내역 저장
applications = {}
 
 
def generate_session_token():
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))
 
def generate_receipt_number():
    return "INS-" + ''.join(random.choices(string.digits, k=8))
 
 
# ── 페이지 라우트 ──────────────────────────────────────────
 
@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

    # login_page 에러시
    return templates.TemplateResponse(request, "login.html", {"error": "아이디 또는 비밀번호가 올바르지 않습니다."})

    # apply_page
    return templates.TemplateResponse(request, "apply.html", {"user_id": user_id})

    # loading_page
    return templates.TemplateResponse(request, "loading.html", {"receipt": receipt})

    # complete_page
    return templates.TemplateResponse(request, "complete.html", {"data": app_data})
 
 
@app.post("/login")
async def login(request: Request, user_id: str = Form(...), password: str = Form(...)):
    if user_id not in USERS or USERS[user_id] != password:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "아이디 또는 비밀번호가 올바르지 않습니다."
        })
 
    token = generate_session_token()
    sessions[token] = {"user_id": user_id}
 
    response = RedirectResponse(url="/apply", status_code=302)
    response.set_cookie(key="session_token", value=token)
    return response
 
 
@app.get("/apply", response_class=HTMLResponse)
async def apply_page(request: Request):
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        return RedirectResponse(url="/")
 
    user_id = sessions[token]["user_id"]
    return templates.TemplateResponse("apply.html", {
        "request": request,
        "user_id": user_id
    })
 
 
@app.post("/apply")
async def apply_submit(
    request: Request,
    name: str = Form(...),
    birth: str = Form(...),
    phone: str = Form(...),
    email: str = Form(...),
    coverage: str = Form(...),
    agree: str = Form(...)
):
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        return RedirectResponse(url="/")
 
    receipt = generate_receipt_number()
    applications[receipt] = {
        "name": name,
        "birth": birth,
        "phone": phone,
        "email": email,
        "coverage": coverage,
        "receipt": receipt
    }
 
    response = RedirectResponse(url=f"/loading?receipt={receipt}", status_code=302)
    return response
 
 
@app.get("/loading", response_class=HTMLResponse)
async def loading_page(request: Request, receipt: str):
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        return RedirectResponse(url="/")
 
    return templates.TemplateResponse("loading.html", {
        "request": request,
        "receipt": receipt
    })
 
 
@app.get("/complete", response_class=HTMLResponse)
async def complete_page(request: Request, receipt: str):
    token = request.cookies.get("session_token")
    if not token or token not in sessions:
        return RedirectResponse(url="/")
 
    app_data = applications.get(receipt, {})
    return templates.TemplateResponse("complete.html", {
        "request": request,
        "data": app_data
    })
 
 
@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("session_token")
    if token in sessions:
        del sessions[token]
    response = RedirectResponse(url="/")
    response.delete_cookie("session_token")
    return response
 
 
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
