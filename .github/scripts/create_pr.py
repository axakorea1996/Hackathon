import os
from github import Github

token     = os.environ["GH_TOKEN"]
repo_name = os.environ["GITHUB_REPOSITORY"]
sha       = os.environ["GITHUB_SHA"][:7]

with open("review_result.txt", "r") as f:
    review = f.read()

g    = Github(token)
repo = g.get_repo(repo_name)

existing_prs = repo.get_pulls(state="open", base="main", head="dev")
existing_pr  = next(iter(existing_prs), None)

pr_body = f"""## AI 코드 리뷰 결과

> 이 PR은 `dev` 브랜치 push를 감지하여 자동 생성되었습니다.
> 커밋: `{sha}` | 리뷰 모델: `google/gemma-4-31b-it`

---

{review}

---

### 배포 절차
1. 위 리뷰 내용 확인
2. 문제 없으면 **Approve** 후 **Merge** 클릭
3. main 머지 시 Render 자동 배포 시작
"""

if existing_pr:
    existing_pr.create_issue_comment(
        f"## 새 커밋 `{sha}` AI 리뷰 결과\n\n{review}"
    )
    print(f"기존 PR #{existing_pr.number} 에 코멘트 추가: {existing_pr.html_url}")
else:
    pr = repo.create_pull(
        title=f"[AI 리뷰] dev → main ({sha})",
        body=pr_body,
        head="dev",
        base="main"
    )
    print(f"PR 생성 완료: {pr.html_url}")
