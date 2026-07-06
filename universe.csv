# 발행사 선제 영업 플랫폼 GitHub Pages 배포 가이드

이 패키지는 `index.html`을 고정 페이지로 배포하고, GitHub Actions가 매일 `daily_snapshot.json`만 갱신하는 구조입니다.

## 0. 준비물

- GitHub 계정
- GitHub CLI (`gh`)
- Git
- API Key 3개
  - `OPENDART_API_KEY`
  - `NAVER_CLIENT_ID`
  - `NAVER_CLIENT_SECRET`

## 1. 원클릭 배포

압축을 풀고 폴더 루트에서 아래를 실행합니다.

```bash
./deploy/publish_github_pages.sh issuer-sales-platform public main
```

스크립트가 수행하는 작업은 다음과 같습니다.

1. GitHub CLI 로그인 확인
2. Git repository 초기화
3. 새 GitHub repository 생성
4. 파일 전체 push
5. GitHub Actions Secrets 등록
6. GitHub Pages를 `main:/` 기준으로 활성화
7. 첫 `daily_snapshot.json` 갱신 workflow 실행

배포 후 기본 URL은 보통 아래 형태입니다.

```text
https://<github_user>.github.io/issuer-sales-platform/
```

## 2. 수동 배포 명령어

자동 스크립트 대신 직접 하려면 아래 순서로 진행합니다.

```bash
gh auth login

git init
git add .
git commit -m "Initial 발행사 선제 영업 플랫폼 정적 MVP"
git branch -M main

gh repo create <github_user>/issuer-sales-platform --public --source=. --remote=origin --push

gh secret set OPENDART_API_KEY --repo <github_user>/issuer-sales-platform
gh secret set NAVER_CLIENT_ID --repo <github_user>/issuer-sales-platform
gh secret set NAVER_CLIENT_SECRET --repo <github_user>/issuer-sales-platform

gh api --method POST repos/<github_user>/issuer-sales-platform/pages \
  -H "Accept: application/vnd.github+json" \
  -f "source[branch]=main" \
  -f "source[path]=/"

gh workflow run update-daily-snapshot.yml --repo <github_user>/issuer-sales-platform
```

이미 Pages가 만들어져 있다면 POST 대신 PUT으로 변경합니다.

```bash
gh api --method PUT repos/<github_user>/issuer-sales-platform/pages \
  -H "Accept: application/vnd.github+json" \
  -f "source[branch]=main" \
  -f "source[path]=/"
```

## 3. 배포 상태 확인

```bash
./deploy/check_github_pages.sh <github_user>/issuer-sales-platform
```

또는 직접 확인합니다.

```bash
gh api repos/<github_user>/issuer-sales-platform/pages --jq '.html_url, .status, .source'
gh secret list --repo <github_user>/issuer-sales-platform
gh run list --repo <github_user>/issuer-sales-platform --limit 5
```

## 4. 매일 자동 업데이트

`.github/workflows/update-daily-snapshot.yml`가 매일 07:00 KST에 실행됩니다.

실행 흐름:

```text
GitHub Actions schedule
→ pip install -r requirements.txt
→ python scripts/generate_daily_snapshot.py
→ daily_snapshot.json 생성
→ 자동 commit
→ GitHub Pages가 같은 URL에서 최신 JSON 표시
```

## 5. 사용자 공유 URL

사용자에게는 GitHub Pages URL 하나만 공유하면 됩니다.

```text
https://<github_user>.github.io/issuer-sales-platform/
```

매일 새 페이지를 만들거나 새 링크를 공유할 필요 없습니다.

## 6. 보안 주의

- API Key는 GitHub Secrets에만 저장합니다.
- `index.html` 또는 `daily_snapshot.json`에 API Key를 넣지 않습니다.
- Public repository로 운영해도 Secrets 값은 페이지 방문자에게 노출되지 않습니다.
- 단, Actions 로그에 API 응답 원문을 과도하게 출력하지 않도록 유지해야 합니다.

