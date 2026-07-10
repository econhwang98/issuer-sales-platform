# 발행사 자금 수요 레이더

고정 HTML + `daily_snapshot.json` 자동 갱신 방식의 외부 배포용 발행사 선제 영업 플랫폼입니다.

이번 버전은 **v12 전문 금융분류 고도화 패치**입니다. 기존처럼 대부분 기업이 `Risk Low / 기초 모니터링`으로만 떨어지는 현상을 줄이기 위해 위험수준, 위험유형, 자금수요, 추천 금융구조, 검토단계를 세분화했습니다.

## v12 고도화 핵심

- Risk Level을 `Low`, `Watch`, `Moderate`, `Elevated`, `High`, `Critical`로 세분화했습니다.
- 기업별 `risk_type`을 추가했습니다.
  - 계속기업/채무불이행 리스크
  - 자본확충·희석 리스크
  - 차입·리파이낸싱 리스크
  - 투자집행/현금흐름 리스크
  - 인수금융/재무부담 리스크
  - PF/우발채무 리스크
  - 업황/운전자본 리스크
  - 성장투자/후속조달 리스크
  - 시장성 조달/자본비율 리스크
  - 기초 신용 모니터링
- 기업별 예상 자금수요 유형을 추가했습니다.
  - 차환/운전자금, 성장 CAPEX, 메자닌/성장자금, 자본확충/재무구조 개선, 인수/투자자금, PF·운영자금/차환, 업황 방어/차환, R&D/자본확충, 구조조정/유동성 방어, 기초 모니터링
- 추천 금융구조를 `ECM/자본확충`, `메자닌`, `시장성 차입/차환`, `담보부/브릿지 차입`, `CAPEX 금융`, `인수/투자금융`, `PF/부동산 금융`, `성장자금/메자닌`, `신용보강 필요 차입`, `구조조정/자본확충`, `정기 모니터링` 등으로 분류합니다.
- 검토 단계를 `1. 긴급 확인`, `2. 즉시 접촉`, `3. 구조 검토`, `4. 관심 관찰`, `5. 정기 모니터링`, `Hold / 원문 확인`으로 제공합니다.
- 한국신용평가, NICE신용평가, 한국기업평가 기준 장기·단기 신용등급을 기업별로 매칭합니다. 매칭값이 없으면 `무등급`으로 표시합니다.
- 조건검색에서 업종, 우선순위, Trigger, Risk, 위험유형, 자금수요, 추천 금융구조, 검토단계, 신용등급, 키워드를 함께 필터링할 수 있습니다.
- 상세 리포트에 Risk Level + Risk Type, 자금수요 유형, 추천 금융구조, 검토 단계, 장기·단기 신용등급, 신평사별 등급, 추천 구조 및 조건, 분석 신뢰도, 주요 근거, 최근 공시/뉴스, 보완 필요 항목을 표시합니다.

## 핵심 파일

```text
index.html
generate_daily_snapshot.py
requirements.txt
.github/workflows/update-daily-snapshot.yml
README.md
credit_ratings_template.csv
```

`daily_snapshot.json`은 GitHub Actions가 매일 자동 생성합니다.

## 배치 실행 방식

GitHub Actions는 매일 한국시간 오전 8시에 실행됩니다.

```yaml
schedule:
  - cron: "0 23 * * *"
```

수동 실행도 가능합니다.

```text
GitHub repository → Actions → 발행사 자금 수요 레이더 daily snapshot update → Run workflow
```

## 필수 GitHub Secrets

Repository에서 아래 메뉴로 들어갑니다.

```text
Settings → Secrets and variables → Actions → New repository secret
```

필수:

```text
OPENDART_API_KEY
NAVER_CLIENT_ID
NAVER_CLIENT_SECRET
```

선택:

```text
KRX_KIND_RSS_URL
FSC_SERVICE_KEY
```

## 신용평가사 등급 매칭

3사 등급은 `credit_ratings.csv` 파일이 있으면 자동으로 매칭됩니다. ZIP에 포함된 `credit_ratings_template.csv`를 내려받아 내용을 채운 뒤 파일명을 `credit_ratings.csv`로 바꿔 저장소 루트에 업로드하면 됩니다.

필수 또는 권장 컬럼:

```text
corp_name
ticker
corp_code
agency
long_term_rating
long_term_outlook
long_term_date
short_term_rating
short_term_outlook
short_term_date
source_url
```

`agency`는 아래 값을 인식합니다.

```text
한국신용평가
NICE신용평가
한국기업평가
```

장기/단기 등급을 한 줄에 같이 넣어도 되고, `rating`, `rating_type`, `rating_date` 형태의 행 단위 데이터도 인식합니다. 매칭 기준은 `corp_code`, `ticker`, 정규화한 `corp_name` 순서입니다.

공식 신평사 페이지의 공개 등급 정보는 각 사의 저작권·이용조건을 확인해야 합니다. 전체 커버리지와 재현성을 위해서는 공식 이용권한이 있는 데이터 또는 내부 관리 CSV를 `credit_ratings.csv`로 넣는 방식을 권장합니다.

## 적용 방법

GitHub 저장소에서 아래 파일을 덮어쓰기 업로드합니다.

```text
index.html
generate_daily_snapshot.py
requirements.txt
.github/workflows/update-daily-snapshot.yml
README.md
credit_ratings_template.csv
```

그 다음 `Commit changes`를 누르고, Actions에서 `발행사 자금 수요 레이더 daily snapshot update`를 수동 실행합니다.

실행 성공 후 `daily_snapshot.json`에서 아래 값을 확인합니다.

```text
policy_version
v2.0-expert-risk-structure-segmentation
```

그리고 아래 필드가 기업별로 생성되는지 확인합니다.

```text
risk_type
funding_need_type
structure_group
action_stage
analysis_confidence
suggested_terms
credit_rating_status
long_term_rating
short_term_rating
credit_rating_agencies
```

페이지 확인 URL:

```text
https://econhwang98.github.io/issuer-sales-platform/
```

브라우저에서 `Ctrl + F5`로 강제 새로고침하면 최신 `index.html`과 `daily_snapshot.json`을 확인할 수 있습니다.

## 운영 주의사항

- API 키와 Secret은 절대 `index.html`, `daily_snapshot.json`, README에 직접 넣지 않습니다.
- 자동 산출값은 영업 검토용 선별 신호입니다. 실행 전 DART 원문, 뉴스 원문, 신용등급 원천자료, 담당자 검토가 필요합니다.
- Actions push 충돌을 줄이기 위해 workflow에는 `fetch-depth: 0`, `git pull --rebase origin main`, JSON 검증 단계가 포함되어 있습니다.
