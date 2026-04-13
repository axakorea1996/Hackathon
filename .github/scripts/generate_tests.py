import os
import openai

with open("diff.txt", "r") as f:
    diff = f.read()

# main.py 전체를 읽어서 AI에게 전달
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
            "content": f"""아래 FastAPI 소스코드를 보고 pytest 테스트를 작성해주세요.

[중요 규칙]
- 반드시 아래 main.py 소스코드에 있는 실제 파라미터명을 그대로 사용
- Form 파라미터명은 소스코드에서 확인 후 정확히 사용 (임의로 바꾸지 말것)
- FastAPI 리다이렉트는 302가 아닌 307일 수 있으므로 in [302, 307] 로 체크
- 쿼리 파라미터가 필수인 엔드포인트는 반드시 포함해서 호출
- from fastapi.testclient import TestClient 사용
- from main import app 으로 임포트
- 마크다운 코드블록 없이 순수 Python 코드만 출력

[main.py 소스코드]
{main_code[:6000]}

[변경사항]
{diff[:2000]}
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
