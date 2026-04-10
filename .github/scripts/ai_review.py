import os
import time
import openai

with open("diff.txt", "r") as f:
    diff = f.read()

if not diff.strip():
    review = "변경사항이 없습니다."
    with open("review_result.txt", "w") as f:
        f.write(review)
    exit(0)

client = openai.OpenAI(
    api_key=os.environ["OPENROUTER_API_KEY"],
    base_url="https://openrouter.ai/api/v1"
)

# free 모델 rate limit 대비 최대 3회 재시도
MAX_RETRY = 3
MODELS = [
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",  # fallback 1
    "meta-llama/llama-3.3-70b-instruct:free",  # fallback 2
]

review = None
for attempt in range(MAX_RETRY):
    model = MODELS[min(attempt, len(MODELS) - 1)]
    try:
        print(f"시도 {attempt + 1}: {model} 사용")
        response = client.chat.completions.create(
            model=model,
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
        print(f"=== {model} 리뷰 결과 ===")
        print(review)
        break

    except openai.RateLimitError:
        wait = 30 * (attempt + 1)
        print(f"Rate limit 발생 → {wait}초 대기 후 재시도")
        time.sleep(wait)

    except Exception as e:
        print(f"오류 발생: {e}")
        time.sleep(10)

if review is None:
    review = "AI 리뷰를 완료하지 못했습니다. rate limit으로 인해 잠시 후 다시 시도해주세요."

with open("review_result.txt", "w") as f:
    f.write(review)
