# -*- coding: utf-8 -*-
"""financial_engine 파서 검증. API 키 없이 합성 응답만으로 돈다.

    py -X utf8 tests/test_financial_engine.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import financial_engine as fe
import scoring_engine as se
from fixtures_dart import ANNUAL, make_fake_api

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


# --- 금액 파싱 ---------------------------------------------------------------
check("괄호는 음수", fe.to_number("(1,234)"), -1234.0)
check("빈 값은 None", fe.to_number("-"), None)
check("삼각 표기는 음수", fe.to_number("△500"), -500.0)
check("숫자 아니면 None", fe.to_number("해당사항없음"), None)

# --- 계정 추출 ---------------------------------------------------------------
acc = fe.parse_period(ANNUAL, "thstrm")
check("총차입금은 개별 계정 합", acc["total_debt"], 450_000_000_000.0)
check("자산총계", acc["total_assets"], 1_000_000_000_000.0)
check("영업활동현금흐름", acc["operating_cash_flow"], 60_000_000_000.0)

acc_bf = fe.parse_period(ANNUAL, "bfefrmtrm")
check("전전기 영업이익 음수", acc_bf["operating_income"], -5_000_000_000.0)

# --- 지표 산출 ---------------------------------------------------------------
r = fe.derive_shard_row("2025(12)", acc)
check("매출액(억)", r["revenue"], 8000.0)
check("EBITDA(억) = 영업이익+감가+무형", r["ebitda"], 750.0)
check("순차입금(억) = 총차입금-현금", r["net_debt"], 4000.0)
check("부채비율", r["debt_ratio"], 150.0)
check("차입금의존도", r["debt_dependency"], 45.0)
check("EBITDA마진", r["ebitda_margin"], 9.4)
check("순차입금/EBITDA", r["net_debt_to_ebitda"], 5.3)
check("EBITDA/금융비용", r["ebitda_to_interest"], 3.8)

# EBITDA가 0 이하이면 배수 지표는 의미가 없으므로 비운다.
loss = dict(acc_bf, operating_income=-500_000_000_000.0)
r_loss = fe.derive_shard_row("2023(12)", loss)
check("EBITDA 적자면 순차입금배수 없음", r_loss["net_debt_to_ebitda"], None)
check("EBITDA 적자면 커버리지 없음", r_loss["ebitda_to_interest"], None)

m = fe.to_financial_metrics(acc)
check("유동비율", m["current_ratio_pct"], 85.7)
check("당좌비율은 재고 제외", m["quick_ratio_pct"], 51.4)
check("이자보상배율", m["interest_coverage"], 2.0)

# --- 샤드 조립 ---------------------------------------------------------------
counter = {}
shard = fe.build_financial_shard("00126380", "테스트", 2025, make_fake_api(counter),
                                 "2026-09-10 08:00", as_of_month=9)
check("연간 5개년", len(shard["annual"]), 5)
check("오래된 순 정렬", [a["period"] for a in shard["annual"]],
      ["2021(12)", "2022(12)", "2023(12)", "2024(12)", "2025(12)"])
check("최신 기간은 분기", shard["latest_period"], "2026 반기")
check("분기 손익은 누적치", shard["quarter"]["revenue"], 4300.0)
check("연결 기준 표기", shard["source"], "OpenDART fnlttSinglAcntAll (CFS)")
check("기업당 호출 3콜", counter["n"], 3)

# 월에 맞는 분기보고서를 먼저 고른다.
check("9월엔 반기부터", [c for c, _, _ in fe.quarter_candidates_for(9)][0], "11012")
check("2월엔 전년 3분기", fe.quarter_candidates_for(2), [("11014", "3분기", -1)])
check("후보는 최대 2개", max(len(fe.quarter_candidates_for(m)) for m in range(1, 13)), 2)

# 분기에 없는 항목은 직전 연간으로 되메워야 룰 점수 근거가 비지 않는다.
result = se.compute_pure_financial_rule_score(se.FinancialMetrics(**shard["metrics"]))
check("8개 항목 모두 평가됨", result.missing_fields, [])
check("점수는 0~100", 0 <= result.score <= 100, True)
print(f"     룰 점수 {result.score} (raw {result.raw_points}/{result.available_max_points})")

check("데이터 없으면 None",
      fe.build_financial_shard("x", "y", 2025, lambda p, q: {"status": "013"}, ""), None)

print()
if FAILURES:
    print(f"=== {len(FAILURES)} FAILED: {FAILURES} ===")
    sys.exit(1)
print("=== ALL PASS ===")
