# -*- coding: utf-8 -*-
"""품질 급락 시 스냅샷을 덮어쓰지 않는지 검증.

    py -X utf8 tests/test_quality_guard.py
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


def issuers(count, mapped_ratio, phone_ratio):
    out = []
    for i in range(count):
        out.append({
            "corp_name": f"회사{i}",
            "industry": "2차전지/전기차" if i < count * mapped_ratio / 100 else "기타/미분류",
            "ir_phone": "02-000-0000" if i < count * phone_ratio / 100 else "",
        })
    return out


os.environ.pop("SKIP_SANITY_CHECK", None)
os.environ["QUALITY_DROP_TOLERANCE_PP"] = "20"

# 직전 기록이 없으면(최초 실행) 통과시킨다.
gen.PREVIOUS_QUALITY = {}
gen.assert_not_degraded(issuers(100, 10, 10))
check("최초 실행은 통과", True, True)

gen.PREVIOUS_QUALITY = {"issuers": 4000, "industry_mapped_ratio": 98.0, "ir_phone_ratio": 99.0}

# 정상 범위
gen.assert_not_degraded(issuers(4000, 97, 98))
check("소폭 변동은 통과", True, True)

# 업종 매핑률 급락
try:
    gen.assert_not_degraded(issuers(4000, 40, 98))
    check("업종 매핑률 급락 시 중단", "통과함", "RuntimeError")
except RuntimeError as exc:
    check("업종 매핑률 급락 시 중단", "업종 매핑률" in str(exc), True)

# 연락처 확보율 급락 (회사개요 조회 실패 시나리오)
try:
    gen.assert_not_degraded(issuers(4000, 97, 5))
    check("연락처 확보율 급락 시 중단", "통과함", "RuntimeError")
except RuntimeError as exc:
    check("연락처 확보율 급락 시 중단", "IR 연락처 확보율" in str(exc), True)

# 기업 수 반토막
try:
    gen.assert_not_degraded(issuers(1000, 97, 98))
    check("기업 수 급감 시 중단", "통과함", "RuntimeError")
except RuntimeError as exc:
    check("기업 수 급감 시 중단", "기업 수" in str(exc), True)

# 의도한 변화라면 우회할 수 있어야 한다
os.environ["SKIP_SANITY_CHECK"] = "1"
gen.assert_not_degraded(issuers(4000, 40, 5))
check("SKIP_SANITY_CHECK로 우회 가능", True, True)
os.environ.pop("SKIP_SANITY_CHECK")

# 허용 폭을 넓히면 통과해야 한다
os.environ["QUALITY_DROP_TOLERANCE_PP"] = "95"
gen.assert_not_degraded(issuers(4000, 40, 5))
check("허용 폭 조절 가능", True, True)

print()
if FAILURES:
    print(f"=== {len(FAILURES)} FAILED: {FAILURES} ===")
    sys.exit(1)
print("=== ALL PASS ===")
