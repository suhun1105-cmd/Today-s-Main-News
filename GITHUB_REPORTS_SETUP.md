# GitHub 리포트 저장 설정

Render 무료 서버는 재시작되면 `reports/` 폴더가 사라질 수 있습니다.
이 설정을 하면 매일 생성된 리포트 HTML을 GitHub 저장소의 `reports/` 폴더에 저장합니다.

## 1. GitHub Personal Access Token 만들기

GitHub에서 Fine-grained personal access token을 생성하세요.

- Repository access: `Only select repositories`
- Repository: `Today-s-Main-News`
- Permissions:
  - Contents: `Read and write`

## 2. Render Environment Variables 추가

Render 서비스의 Environment에 아래 값을 추가하세요.

```text
GITHUB_TOKEN=발급받은 토큰
GITHUB_REPO=suhun1105-cmd/Today-s-Main-News
GITHUB_BRANCH=main
```

`GITHUB_TOKEN`은 비밀번호처럼 취급해야 합니다. GitHub에 커밋하거나 공개하면 안 됩니다.
