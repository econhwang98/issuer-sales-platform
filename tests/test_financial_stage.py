# -*- coding: utf-8 -*-
"""재무 수집 단계(collect_financial_shards) 검증. API 키 없이 돈다.

    py -X utf8 tests/test_financial_stage.py
"""
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

from fixtures_dart import ANNUAL, QUARTER

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


TMP = tempfile.mkdtemp(prefix="finshard_")
gen.FINANCIAL_DIR = pathlib.Path(TMP)

calls = {"n": 0}


def fake_opendart_get(path, params, timeout=30):
    calls["n"] += 1
    if params.get("fs_div") == "CFS" and params.get("reprt_code") == "11011":
        return {"status": "000", "list": ANNUAL}
    if params.get("fs_div") == "CFS" and params.get("reprt_code") == "11012":
        return {"status": "000", "list": QUARTER}
    return {"status": "013"}


gen.opendart_get = fake_opendart_get
gen.requests = object()  # requests가 설치된 것처럼
os.environ["OPENDART_API_KEY"] = "test-key"
os.environ["FINANCIAL_SHARD_LIMIT"] = "2"
os.environ["API_SLEEP_SECONDS"] = "0"

now = datetime(2026, 9, 10, 8, 0, tzinfo=timezone(timedelta(hours=9)))


def make_issuer(code, name, score):
    return {
        "corp_code": code, "corp_name": name,
        "final_score": score, "pure_financial_rule_score": 55.0,
        "ai_base_score": 70.0, "news_trigger_score": 80.0,
        "financial_basis": "대체지표", "financial_period": "",
        "score_band": gen.score_band(score),
        "missing_fields": ["상세 재무제표 보강 필요"],
        "rule_breakdown": [], "source_status": {"dart_financial": "fast_mode_deferred"},
    }


issuers = [make_issuer("00100001", "가나전자", 82.0),
           make_issuer("00100002", "다라화학", 78.0),
           make_issuer("00100003", "마바산업", 60.0)]

stats = gen.collect_financial_shards(issuers, now)

check("한도만큼만 시도", stats["attempted"], 2)
check("샤드 2건 기록", stats["written"], 2)
check("실패 없음", stats["failed"], 0)
check("1위 근거가 재무제표로 바뀜", issuers[0]["financial_basis"], "재무제표")
check("한도 밖 기업은 그대로", issuers[2]["financial_basis"], "대체지표")
check("최신 재무 기간 기록", issuers[0]["financial_period"], "2026 반기")
check("샤드 경로 기록", issuers[0]["financial_shard"], "financials/00100001.json")
check("보완 항목이 실제 결측으로 갱신", issuers[0]["missing_fields"], [])
check("룰 근거 8개 기록", len(issuers[0]["rule_breakdown"]), 8)

rule = issuers[0]["pure_financial_rule_score"]
check("대체 점수 55에서 벗어남", rule != 55.0, True)
check("최종 점수 재계산", issuers[0]["final_score"],
      gen.compute_final_funding_score(rule, 70.0, 80.0))
print(f"     룰 {rule} → 최종 {issuers[0]['final_score']} (대체지표 시절 82.0)")

check("파일 2개 기록", sorted(os.listdir(TMP)), ["00100001.json", "00100002.json"])
shard = json.loads(io.open(os.path.join(TMP, "00100001.json"), encoding="utf-8").read())
check("샤드에 5개년", len(shard["annual"]), 5)

# 갱신 주기 안이면 API를 다시 부르지 않는다.
calls_before = calls["n"]
for issuer in issuers:
    issuer["financial_basis"] = "대체지표"
stats2 = gen.collect_financial_shards(issuers, now)
check("두 번째 실행은 캐시 재사용", stats2["reused"], 2)
check("추가 API 호출 없음", calls["n"], calls_before)
check("재사용해도 근거는 재무제표", issuers[0]["financial_basis"], "재무제표")

# 파싱 규칙이 바뀌면(schema_version 상승) 캐시 기간이 남았어도 다시 받아야 한다.
import financial_engine as fe
for issuer in issuers:
    issuer["financial_basis"] = "대체지표"
for name in os.listdir(TMP):
    fp = os.path.join(TMP, name)
    obj = json.loads(io.open(fp, encoding="utf-8").read())
    obj["schema_version"] = fe.SHARD_SCHEMA_VERSION - 1   # 구버전으로 되돌림
    io.open(fp, "w", encoding="utf-8").write(json.dumps(obj, ensure_ascii=False))
calls_before = calls["n"]
stats3 = gen.collect_financial_shards(issuers, now)
check("구버전 샤드는 재수집", stats3["written"], 2)
check("구버전 샤드는 재사용 안 함", stats3["reused"], 0)
check("API를 다시 호출", calls["n"] > calls_before, True)
check("새로 쓴 샤드는 최신 버전",
      json.loads(io.open(os.path.join(TMP, "00100001.json"), encoding="utf-8").read())["schema_version"],
      fe.SHARD_SCHEMA_VERSION)

os.environ["FINANCIAL_SHARD_LIMIT"] = "0"
check("한도 0이면 비활성", gen.collect_financial_shards(issuers, now)["status"], "disabled")

os.environ["FINANCIAL_SHARD_LIMIT"] = "2"
os.environ["OPENDART_API_KEY"] = ""
check("키 없으면 건너뜀", gen.collect_financial_shards(issuers, now)["status"], "api_key_missing")

shutil.rmtree(TMP, ignore_errors=True)

print()
if FAILURES:
    print(f"=== {len(FAILURES)} FAILED: {FAILURES} ===")
    sys.exit(1)
print("=== ALL PASS ===")
