# 발행사 선제 영업 플랫폼 정적 MVP v3 API 확장판

고정 HTML + `daily_snapshot.json` 자동 갱신 방식의 외부 배포용 MVP입니다.  
이번 버전은 **매일 08:00 KST 자동 배치**와 **OpenDART 재무/이벤트 공시 + 네이버 뉴스 고도화 + 선택형 KIND RSS**를 반영했습니다.

## 1. 핵심 구조

```text
index.html             # 사용자에게 공유하는 고정 페이지
daily_snapshot.json    # 매일 자동 갱신되는 데이터 스냅샷
universe.csv           # 스크리닝 대상 기업 목록
scripts/               # 스냅샷 생성/스코어링 스크립트
.github/workflows/     # GitHub Actions 일일 자동 갱신
```

사용자는 매일 같은 URL만 접속합니다. 매일 새 페이지를 공유하지 않습니다.

```text
https://econhwang98.github.io/issuer-sales-platform/
```

## 2. 자동 배치

GitHub Actions의 schedule은 UTC 기준입니다. 한국시간 오전 8시는 전날 UTC 23:00이므로 아래 cron을 사용합니다.

```yaml
schedule:
  - cron: "0 23 * * *"
```

수동 실행도 가능합니다.

```text
GitHub repository → Actions → 발행사 선제 영업 플랫폼 일일 스냅샷 업데이트 → Run workflow
```

## 3. 확정 점수 정책

```text
Final Funding Score
= 45% × Pure Financial Rule Score
+ 40% × AI Base Score
+ 15% × News Trigger Score
```

- 뉴스/공시 이벤트는 `News Trigger Score`로 별도 산출합니다.
- 뉴스는 최종 점수에 15%만 반영합니다.
- Pure Financial Rule에는 뉴스 항목을 넣지 않습니다.
- 당좌비율 기준은 사용자 확정에 따라 `100% 미만`으로 반영했습니다.
- DART 장애/점검에 대비해 `source_status`, `missing_fields`, raw/cache 저장 구조를 전제로 합니다.

## 4. 이번 버전에 반영된 데이터 소스

### 필수 라이브 소스

```text
OPENDART_API_KEY
NAVER_CLIENT_ID
NAVER_CLIENT_SECRET
```

### 선택 소스

```text
KRX_KIND_RSS_URL
FSC_SERVICE_KEY
```

`KRX_KIND_RSS_URL`은 KIND 화면에서 RSS 주소를 복사해 넣으면 DART 보완 신호로 사용합니다. 없어도 정상 동작합니다.  
`FSC_SERVICE_KEY`는 금융위원회/공공데이터포털 기업정보 API 확장을 위한 Hook으로 열어두었습니다. 이번 정적 MVP에서는 스코어 산식에 직접 반영하지 않습니다.

## 5. 라이브 처리 내용

### OpenDART 재무제표

`fnlttSinglAcntAll.json`으로 최근 사업연도 연결 재무제표를 조회하고 아래 항목을 best-effort 파싱합니다.

```text
자산총계, 부채총계, 유동자산, 유동부채, 재고자산,
차입금/사채, 영업이익, 이자비용,
영업활동현금흐름, 투자활동현금흐름, 현금 순증감
```

### OpenDART 이벤트 공시

`list.json` 공시검색으로 최근 45일 공시 제목을 조회하고 아래 이벤트를 감지합니다.

```text
유상증자, 전환사채, 신주인수권부사채, 교환사채,
회사채/사채, 단기차입금, 채무보증, 담보제공,
타법인 주식 및 출자증권 취득, 유형자산 취득,
주요사항보고서, 증권신고서
```

### 네이버 뉴스 API 고도화

기업명 단일 검색이 아니라 아래 쿼리 조합을 실행합니다.

```text
기업명 + 유상증자
기업명 + CB / 전환사채 / BW
기업명 + 회사채 / CP / 차입 / 리파이낸싱
기업명 + 공장 증설 / CAPEX / 대규모 투자
기업명 + 신용등급 / 유동성 / 자본잠식 / 적자 / PF
산업명 + 업황 둔화 / 스프레드 축소 / 원가 상승
```

## 6. 네가 지금 GitHub에서 해야 할 일

기존 저장소에는 이미 페이지가 있으므로, 아래 파일/폴더를 **덮어쓰기 업로드**하면 됩니다.

```text
index.html
README.md
GITHUB_PAGES_DEPLOY_GUIDE.md
API_EXPANSION_NOTES.md
.env.example
requirements.txt
scripts/
.github/
daily_snapshot.json
```

가장 중요한 파일은 아래 2개입니다.

```text
scripts/generate_daily_snapshot.py
.github/workflows/update-daily-snapshot.yml
```

## 7. GitHub Secrets

Repository에서 아래로 들어갑니다.

```text
Settings → Secrets and variables → Actions → New repository secret
```

필수 3개를 등록합니다.

```text
OPENDART_API_KEY
NAVER_CLIENT_ID
NAVER_CLIENT_SECRET
```

선택 2개는 나중에 등록해도 됩니다.

```text
KRX_KIND_RSS_URL
FSC_SERVICE_KEY
```

## 8. Run workflow 확인

Secrets 등록 후:

```text
Actions → 발행사 선제 영업 플랫폼 일일 스냅샷 업데이트 → Run workflow
```

성공하면 `daily_snapshot.json`이 갱신되고, 페이지에서 `Ctrl + F5`로 새로고침하면 최신 결과가 보입니다.

## 9. 운영상 주의사항

- API 키는 절대 `index.html`, `daily_snapshot.json`, README 등에 직접 넣지 않습니다.
- GitHub Secrets에만 저장합니다.
- `daily_snapshot.json`에는 공개 가능한 요약·점수·근거만 저장합니다.
- 투자·대출·영업 실행 전에는 DART 원문, 뉴스 원문, 신용등급 원천파일을 담당자가 확인해야 합니다.
- 자동 산출값은 `source_status`, `missing_fields`, `policy_version`과 함께 확인해야 합니다.
