import os
import openai

with open("diff.txt", "r") as f:
    diff = f.read()

if not diff.strip():
    review = "변경사항이 없습니다."
else:
    client = openai.OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1"
    )

    response = client.chat.completions.create(
        model="google/gemma-4-31b-it:free",
        messages=[
            {
                "role": "user",
                "content": f"""다음 코드 변경사항을 리뷰해주세요.

리뷰 항목:
1. 버그 또는 잠재적 오류
2. 보안 취약점
3. 코드 품질 및 가독성
4. 개선 제안

변경사항:
{diff[:6000]}

한국어로 작성해주세요. 마크다운 형식으로 작성해주세요."""
            }
        ],
        max_tokens=2000
    )

    review = response.choices[0].message.content
    print("=== AI 리뷰 결과 ===")
    print(review)

with open("review_result.txt", "w") as f:
    f.write(review)
