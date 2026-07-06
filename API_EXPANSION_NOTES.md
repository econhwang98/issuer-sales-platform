# 발행사 선제 영업 플랫폼 API 확장 반영 내역 v3

## 이번 버전에 반영된 API/데이터 소스

### 1. OpenDART 재무제표
- `fnlttSinglAcntAll.json` 기반으로 자산총계, 부채총계, 유동자산, 유동부채, 재고자산, 차입금/사채, 영업이익, 이자비용, 현금흐름 항목을 best-effort 파싱합니다.
- 파싱 실패 항목은 `missing_fields`와 `source_status`에 남깁니다.
- 당좌비율 기준은 사용자 확정에 따라 `100% 미만`으로 반영했습니다.

### 2. OpenDART 이벤트 공시
- `list.json` 공시검색으로 최근 45일 공시 제목을 수집합니다.
- 유상증자, 전환사채, 신주인수권부사채, 교환사채, 회사채/사채, 단기차입금, 타법인 취득, 유형자산 취득, 주요사항보고서, 증권신고서를 이벤트 신호로 반영합니다.
- 이벤트 공시는 `News Trigger Score`의 event severity에 반영됩니다.

### 3. 네이버 뉴스 API 고도화
- 단순 기업명 검색이 아니라 기업명 + 자금조달/유동성/투자 이벤트 키워드 조합으로 여러 쿼리를 실행합니다.
- 산업명 + 업황 둔화/스프레드 축소/원가 상승 등 산업 압박 쿼리도 함께 실행합니다.
- 뉴스는 별도 `News Trigger Score`로 분리하고 최종점수에는 15%만 반영합니다.

### 4. KIND RSS 선택 연동
- GitHub Secret `KRX_KIND_RSS_URL`에 KIND에서 복사한 RSS 주소를 넣으면 선택적으로 사용됩니다.
- 기본값은 `not_configured`이며, 없어도 서비스는 정상 동작합니다.

### 5. 금융위원회/공공데이터포털 API Hook
- `FSC_SERVICE_KEY`는 환경변수로 받을 수 있게 열어뒀습니다.
- 다만 기관별 응답 필드/승인상태 차이가 있어 이번 정적 MVP에서는 실제 스코어 산식에는 아직 넣지 않고 `reserved_hook_not_enabled`로 표시합니다.

## 자동 배치

GitHub Actions schedule은 UTC 기준이므로, 한국시간 오전 8시는 `0 23 * * *` 입니다.

```yaml
on:
  schedule:
    - cron: "0 23 * * *"
```

## 점수 구조

```text
Final Funding Score
= 45% × Pure Financial Rule Score
+ 40% × AI Base Score
+ 15% × News Trigger Score
```

뉴스와 공시 이벤트는 Rule Score에 중복 반영하지 않고 `News Trigger Score`로 분리합니다.
