import os
import openai

with open("diff.txt", "r") as f:
    diff = f.read()

# main.py 전체 내용도 읽어서 컨텍스트 제공
try:
    with open("main.py", "r") as f:
        main_code = f.read()
except:
    main_code = ""

if not diff.strip():
    print("변경사항 없음 - 테스트 생성 스킵")
    with open("generated_tests.py", "w") as f:
        f.write("# 변경사항 없음\n")
    exit(0)

client = openai.OpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1"
)

response = client.chat.completions.create(
    model="google/gemma-4-31b-it",
    messages=[
        {
            "role": "user",
            "content": f"""아래는 FastAPI로 만든 보험 청약 웹 애플리케이션입니다.
변경사항을 보고 실제 존재하는 엔드포인트에 대한 pytest 테스트를 작성해주세요.

[실제 엔드포인트 목록]
- GET  /          → 로그인 페이지 (200 반환)
- POST /login     → 로그인 처리 (성공 시 302, 실패 시 200)
- GET  /apply     → 청약 페이지 (로그인 필요, 미로그인 시 302)
- POST /apply     → 청약 제출
- GET  /loading   → 로딩 페이지
- GET  /complete  → 완료 페이지
- GET  /logout    → 로그아웃
- GET  /admin     → 관리자 페이지 (admin 계정만)
- GET  /register  → 회원가입 페이지
- POST /register  → 회원가입 처리

[테스트 작성 규칙]
- from fastapi.testclient import TestClient 사용
- from main import app 으로 임포트
- TestClient(app) 으로 클라이언트 생성
- 실제 존재하는 엔드포인트만 테스트
- 각 함수에 한국어 docstring 작성
- import 포함한 완전한 코드 출력
- 마크다운 코드블록 없이 순수 Python 코드만 출력
- DATABASE_URL 환경변수가 없을 수 있으므로 DB 연결 실패를 graceful하게 처리

[변경사항]
{diff[:4000]}
"""
        }
    ],
    max_tokens=3000
)

test_code = response.choices[0].message.content
test_code = test_code.replace("```python", "").replace("```", "").strip()

print("=== 생성된 테스트 코드 ===")
print(test_code)

with open("generated_tests.py", "w") as f:
    f.write(test_code)
