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

# --- 계정명 변형 (실제 DART 데이터에서 놓쳤던 것들) ------------------------
# 정확일치 매칭일 때 실측 86.4% 기간의 EBITDA가 EBIT과 같아졌다(상각비 미매칭).
from fixtures_dart import row as _row

VARIANTS = [
    _row("BS", "ifrs-full_Assets", "자산총계", "1,000,000,000,000"),
    _row("IS", "dart_OperatingIncomeLoss", "영업이익", "40,000,000,000"),
    # 상각비 표기 변형 — 모두 더해져야 한다
    _row("CF", "", "유형자산감가상각비", "30,000,000,000"),
    _row("CF", "", "사용권자산상각비", "6,000,000,000"),
    _row("CF", "", "무형자산상각비", "4,000,000,000"),
    # 더하면 안 되는 것들
    _row("CF", "", "대손상각비", "9,000,000,000"),
    _row("CF", "", "감가상각누계액환입", "5,000,000,000"),
    # 차입금 표기 변형 — 모두 더해져야 한다
    _row("BS", "", "유동성장기차입금", "100,000,000,000"),
    _row("BS", "", "장기차입금", "200,000,000,000"),
    _row("BS", "", "차입부채", "50,000,000,000"),
    _row("BS", "", "유동리스부채", "10,000,000,000"),
    # 차감계정이라 더하면 이중계상
    _row("BS", "", "사채할인발행차금", "-3,000,000,000"),
    _row("BS", "", "전환권조정", "-2,000,000,000"),
]
va = fe.parse_period(VARIANTS, "thstrm")
check("상각비 변형 3종 합산", va["dep_amort"], 40_000_000_000.0)
check("대손상각비는 제외", va["dep_amort"] != 49_000_000_000.0, True)
check("차입금 변형 4종 합산", va["total_debt"], 360_000_000_000.0)
check("사채 차감계정 제외", va["total_debt"] != 355_000_000_000.0, True)

vr = fe.derive_shard_row("2025(12)", va)
check("EBITDA = EBIT + 상각비", vr["ebitda"], 800.0)
check("EBITDA != EBIT", vr["ebitda"] != vr["ebit"], True)

# 상각비가 아예 없으면 EBITDA = EBIT (정상 동작)
none_dep = fe.parse_period([_row("IS", "dart_OperatingIncomeLoss", "영업이익", "40,000,000,000")], "thstrm")
check("상각비 없으면 dep_amort None", none_dep["dep_amort"], None)

# --- 손익계산서가 빈 연도는 다른 재무제표 구분으로 채운다 ------------------
# 연도별로 연결/별도 제출이 갈리면 재무상태표만 있고 손익이 비는 구간이 생긴다.
BS_ONLY = [
    _row("BS", "ifrs-full_Assets", "자산총계", "500,000,000,000", "480,000,000,000", "460,000,000,000"),
    _row("BS", "ifrs-full_Liabilities", "부채총계", "300,000,000,000", "290,000,000,000", "280,000,000,000"),
    _row("BS", "ifrs-full_Equity", "자본총계", "200,000,000,000", "190,000,000,000", "180,000,000,000"),
]
IS_ONLY = [
    _row("BS", "ifrs-full_Assets", "자산총계", "500,000,000,000", "480,000,000,000", "460,000,000,000"),
    _row("IS", "ifrs-full_Revenue", "매출액", "90,000,000,000", "85,000,000,000", "80,000,000,000"),
    _row("IS", "dart_OperatingIncomeLoss", "영업이익", "9,000,000,000", "8,000,000,000", "7,000,000,000"),
    _row("IS", "ifrs-full_ProfitLoss", "당기순이익", "6,000,000,000", "5,000,000,000", "4,000,000,000"),
]

def split_api(path, params):
    # CFS는 재무상태표만, OFS는 손익까지 준다
    if params["reprt_code"] != fe.ANNUAL_REPRT:
        return {"status": "013"}
    return {"status": "000", "list": BS_ONLY if params["fs_div"] == "CFS" else IS_ONLY}

gap = fe.build_financial_shard("00999999", "구멍", 2025, split_api, "2026-09-11", as_of_month=9)
first = gap["annual"][-1]
check("빈 손익을 다른 구분으로 채움", first["revenue"], 900.0)
check("영업이익도 채워짐", first["ebit"], 90.0)
check("당기순이익도 채워짐", first["net_income"], 60.0)
check("재무상태표 값은 유지", first["debt_ratio"], 150.0)

# 상각비를 못 구하면 EBITDA를 만들지 않는다
check("상각비 없으면 EBITDA 없음", first["ebitda"], None)
check("EBITDA 파생 지표도 없음", first["ebitda_margin"], None)

check("데이터 없으면 None",
      fe.build_financial_shard("x", "y", 2025, lambda p, q: {"status": "013"}, ""), None)

print()
if FAILURES:
    print(f"=== {len(FAILURES)} FAILED: {FAILURES} ===")
    sys.exit(1)
print("=== ALL PASS ===")
