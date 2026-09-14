# -*- coding: utf-8 -*-
"""이벤트 점수와 Trigger 분류 검증. API 키 없이 돈다.

    py -X utf8 tests/test_event_scoring.py
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

spec = importlib.util.spec_from_file_location("gen", os.path.join(REPO, "generate_daily_snapshot.py"))
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

FAILURES = []


def check(label, got, want):
    if got == want:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}: got={got!r} want={want!r}")
        FAILURES.append(label)


S = gen.EVENT_SEVERITY

# --- 이미 실행된 조달은 선제 영업 대상이 아니다 --------------------------------
# 유상증자·전환사채·회사채 공시 시점에는 주관사와 투자자가 이미 정해져 있다.
# 예전에는 이들이 최고점(100/95/90)이라 상위 100개사 중 81건을 차지했다.
for name in ("유상증자", "전환사채", "회사채", "CP", "교환사채"):
    check(f"{name}은 선행 신호보다 낮다", S[name] < S["대규모 투자"], True)
    check(f"{name}은 신용등급 하향보다 낮다", S[name] < S["신용등급 하향"], True)

check("유상증자 45점", S["유상증자"], 45)
check("회사채 38점", S["회사채"], 38)

# --- 앞으로 자금이 필요해질 신호가 위로 --------------------------------------
check("신용등급 하향이 유상증자보다 높다", S["신용등급 하향"] > S["유상증자"], True)
check("대규모 투자가 회사채보다 높다", S["대규모 투자"] > S["회사채"], True)
check("타법인 취득이 전환사채보다 높다", S["타법인 주식"] > S["전환사채"], True)
check("채무보증이 CP보다 높다", S["채무보증"] > S["CP"], True)

# --- 긴급 신용 이벤트이 최상위 -------------------------------------------------
for name in ("채무불이행", "자본잠식", "회생절차"):
    check(f"{name} 100점", S[name], 100)

# --- Trigger 분류 --------------------------------------------------------------
CASES = [
    (["주요사항보고서(유상증자결정)"], "자금조달 직접공시"),
    (["타인에대한채무보증결정"], "차입/담보 이벤트"),
    (["신규 시설투자 등 결정"], "투자·CAPEX 이벤트"),
    (["타법인 주식 및 출자증권 취득결정"], "M&A/지분투자 이벤트"),
    (["신용등급 하향 조정"], "신용등급 변동"),
    (["주요사항보고서(회생절차개시신청)"], "신용/계속기업 리스크"),
    ([], "재무구조 점검"),
    ([""], "재무구조 점검"),
]
for titles, want in CASES:
    check(f"분류: {titles or '이벤트 없음'}", gen.trigger_type_from(0, 0, titles), want)

# 한 공시에 조달과 투자가 섞이면 앞으로의 자금 수요 쪽으로 본다.
check("투자가 조달보다 먼저 판정",
      gen.trigger_type_from(0, 0, ["신규 시설투자 결정 및 유상증자결정"]),
      "투자·CAPEX 이벤트")

print()
if FAILURES:
    print(f"=== {len(FAILURES)} FAILED: {FAILURES} ===")
    sys.exit(1)
print("=== ALL PASS ===")
