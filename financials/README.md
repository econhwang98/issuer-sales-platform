# financials/ — 기업별 재무 샤드

기업 검토보고서(`index.html`의 "검토보고서 생성 · PDF")가 읽는 파일입니다.
프론트엔드는 `financials/{corp_code}.json` 하나만 가져가므로, 17MB짜리
`daily_snapshot.json`을 건드리지 않고 필요한 기업만 내려받습니다.

파일이 없으면(404) 보고서는 "재무 데이터 미수집 구간" 안내를 띄우고
공시·뉴스 기반 축약본으로 생성됩니다. 즉 **샤드가 없어도 기능은 동작**하며,
파이프라인이 우선순위 상위 기업부터 단계적으로 채워 넣으면 됩니다.

## 스키마

```json
{
  "corp_code": "01160363",
  "corp_name": "에코프로비엠",
  "unit": "억원",
  "latest_period": "2026(06) 반기",
  "source": "OpenDART fnlttSinglAcntAll (CFS)",
  "fetched_at": "2026-09-09 10:03",

  "annual": [
    {
      "period": "2025(12)",
      "revenue": 41250, "ebit": -430, "ebitda": 1980, "net_income": -880,
      "total_assets": 81330, "total_debt": 34880, "net_debt": 28110,
      "ebit_margin": -1.0, "ebitda_margin": 4.8,
      "ebitda_to_interest": 1.6, "net_debt_to_ebitda": 14.2,
      "debt_ratio": 178.4, "debt_dependency": 42.9
    }
  ],

  "quarter": { "period": "2026(06)", "...": "annual과 동일한 키" },

  "maturity": {
    "as_of": "2026년 6월말 잔액",
    "rows": [
      { "label": "단기차입금", "balance": 9840, "within_1y": 9840, "over_1y": 0 }
    ]
  }
}
```

- `annual`은 오래된 연도부터 정렬한다. 보고서 표는 배열 순서를 그대로 쓴다.
- 금액 단위는 `unit`(기본 억원)으로 통일한다. 비율은 %, 배수는 배.
- `quarter`는 최신 분기·반기 누적치. 없으면 생략 가능하다.
- `maturity`는 선택 항목. 없으면 보고서에서 만기구조 표가 빠진다.
- `latest_period`와 `fetched_at`은 보고서 재생성 판단(지문)에 쓰이므로 반드시 채운다.
  이 두 값이 그대로면 보고서는 다시 만들지 않고 기존 것을 그대로 제공한다.

## 수집 정책

`fnlttSinglAcntAll`은 1회 호출로 당기·전기·전전기 3개년을 반환한다.
따라서 기업당 연간 2콜 + 분기 1콜 = 3콜이면 5개년 + 최신분기를 채운다.
OpenDART 일일 한도(20,000콜)를 고려해 우선순위 상위 기업부터 채우고,
나머지는 순회 방식으로 넓혀 간다.
