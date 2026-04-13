import os
import sys
from github import Github

token     = os.environ["GH_TOKEN"]
repo_name = os.environ["GITHUB_REPOSITORY"]
sha       = os.environ["GITHUB_SHA"][:7]
result    = sys.argv[1]  # "pass" or "fail"

with open("test_output.txt", "r") as f:
    output = f.read()

g    = Github(token)
repo = g.get_repo(repo_name)

existing_prs = repo.get_pulls(state="open", base="main", head="dev")
existing_pr  = next(iter(existing_prs), None)

if result == "pass":
    icon = "✅"
    title = "테스트 통과"
else:
    icon = "❌"
    title = "테스트 실패"

comment = f"""## {icon} 자동 테스트 결과 — {title}

> 커밋: `{sha}`

<details>
<summary>테스트 실행 로그 보기</summary>

```
{output[:3000]}
```

</details>
"""

if existing_pr:
    existing_pr.create_issue_comment(comment)
    print(f"PR #{existing_pr.number} 테스트 결과 코멘트 추가")
else:
    print("열린 PR 없음 - 코멘트 스킵")
