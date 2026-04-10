import os
import openai

with open("diff.txt", "r") as f:
    diff = f.read()

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
            "content": f"""다음 FastAPI 코드 변경사항을 보고 pytest 테스트 코드를 작성해주세요.

규칙:
- pytest와 httpx를 사용하는 FastAPI TestClient 방식으로 작성
- 정상 케이스와 오류 케이스 모두 포함
- 각 테스트 함수에 한국어 docstring 작성
- import 구문 포함
- 실행 가능한 완전한 코드만 출력
- 마크다운 코드블록 없이 순수 Python 코드만 출력

변경사항:
{diff[:5000]}"""
        }
    ],
    max_tokens=3000
)

test_code = response.choices[0].message.content

# 마크다운 코드블록 제거
test_code = test_code.replace("```python", "").replace("```", "").strip()

print("=== 생성된 테스트 코드 ===")
print(test_code)

with open("generated_tests.py", "w") as f:
    f.write(test_code)
