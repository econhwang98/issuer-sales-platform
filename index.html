#!/usr/bin/env python3
"""Generate 발행사 선제 영업 플랫폼 daily_snapshot.json.

This script is designed for server-side execution only.
Never place API keys in index.html or daily_snapshot.json.

Required for live mode:
- OPENDART_API_KEY
- NAVER_CLIENT_ID
- NAVER_CLIENT_SECRET

Without API keys, it produces a deterministic sample snapshot so the page remains testable.
"""
from __future__ import annotations
import csv
import json
import os
import re
import time
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # allows linting without deps
    requests = None

from scoring_engine import compute_final_funding_score, compute_news_trigger_score, priority_bucket

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "universe.csv"
OUTPUT_PATH = ROOT / "daily_snapshot.json"

NEGATIVE_TERMS = ["적자", "부진", "하락", "둔화", "차입", "리파이낸싱", "유동성", "채무", "부도", "자본잠식", "등급 하향", "PF", "만기", "손상"]
POSITIVE_TERMS = ["수주", "흑자", "증설", "투자", "성장", "개선", "턴어라운드", "계약", "증가"]
EVENT_SEVERITY = {
    "유상증자": 100, "채무불이행": 100, "자본잠식": 100, "전환사채": 95, "신주인수권부사채": 95,
    "회사채": 90, "CP": 90, "리파이낸싱": 90, "대규모 투자": 90, "CAPEX": 90, "증설": 85,
    "신용등급 하향": 80, "PF": 80, "업황 둔화": 60, "원가 상승": 60
}


def now_kst() -> datetime:
    return datetime.now(KST)


def load_universe() -> List[Dict[str, str]]:
    with UNIVERSE_PATH.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def naver_news_search(query: str, display: int = 20) -> List[Dict[str, Any]]:
    if requests is None:
        return []
    client_id = os.getenv("NAVER_CLIENT_ID")
    client_secret = os.getenv("NAVER_CLIENT_SECRET")
    if not client_id or not client_secret:
        return []
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
    params = {"query": query, "display": display, "sort": "date"}
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("items", [])


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.replace("&quot;", '"').replace("&amp;", "&")


def classify_news(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    corp_pos = corp_neg = ind_pos = ind_neg = 0
    max_event = 0
    key_news = []
    seen = set()
    for item in items[:30]:
        title = strip_html(item.get("title", ""))
        desc = strip_html(item.get("description", ""))
        text = f"{title} {desc}"
        if title in seen:
            continue
        seen.add(title)
        neg = sum(1 for w in NEGATIVE_TERMS if w in text)
        pos = sum(1 for w in POSITIVE_TERMS if w in text)
        corp_neg += 1 if neg > pos else 0
        corp_pos += 1 if pos > neg else 0
        for event, severity in EVENT_SEVERITY.items():
            if event in text:
                max_event = max(max_event, severity)
        if neg or pos or max_event:
            key_news.append({"title": title[:90], "source": "naver_news", "sentiment": "negative" if neg > pos else "positive" if pos > neg else "mixed", "severity": max_event or 50})
    trigger = compute_news_trigger_score(max_event or None, corp_pos, corp_neg, ind_pos, ind_neg)
    return {"score": trigger, "corp_positive": corp_pos, "corp_negative": corp_neg, "industry_positive": ind_pos, "industry_negative": ind_neg, "event_severity": max_event, "key_news": key_news[:3]}


def dart_single_company_all_accounts(corp_code: str, year: int, reprt_code: str = "11011", fs_div: str = "CFS") -> Dict[str, Any]:
    if requests is None:
        return {"status": "requests_missing", "list": []}
    key = os.getenv("OPENDART_API_KEY")
    if not key:
        return {"status": "api_key_missing", "list": []}
    url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
    params = {"crtfc_key": key, "corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def mock_scores_for(row: Dict[str, str], idx: int) -> Dict[str, Any]:
    # Deterministic placeholder for MVP test mode. Replace with parsed DART metrics in live mode.
    samples = [
        (78.0, 84.0, 92.0, "Mid", "CP + 담보부 라인", "9M · 6.40% 내외"),
        (66.0, 79.0, 88.0, "Low", "Credit Loan / 운영자금 라인", "1Y · 변동금리 + 약정"),
        (61.0, 65.0, 67.0, "Mid", "만기/금리 모니터링 후 제안", "Review after next filing"),
        (58.0, 64.0, 68.0, "Mid", "회사채/CP 시장 모니터링", "시장성 조달 조건 확인"),
        (54.0, 50.0, 62.0, "High", "Other / Manual Review", "투자심의 전 별도 검토"),
    ]
    return dict(zip(["rule", "ai", "news", "risk", "structure", "terms"], samples[idx % len(samples)]))


def build_snapshot() -> Dict[str, Any]:
    t = now_kst()
    rows = load_universe()
    issuers = []
    source_status_global = {"dart": "api_key_missing", "news": "api_key_missing", "credit_rating": "manual_file_needed", "llm_annotation": "sample_placeholder"}
    for i, row in enumerate(rows):
        query = row.get("keywords") or row["corp_name"]
        news_items = []
        if os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"):
            try:
                news_items = naver_news_search(query)
                source_status_global["news"] = "live_ok"
                time.sleep(0.15)
            except Exception as exc:
                source_status_global["news"] = f"error:{type(exc).__name__}"
        classified = classify_news(news_items) if news_items else None
        mock = mock_scores_for(row, i)
        news_score = classified["score"] if classified else mock["news"]
        final_score = compute_final_funding_score(mock["rule"], mock["ai"], news_score)
        issuers.append({
            "rank": 0,
            "corp_name": row["corp_name"], "corp_code": row["corp_code"], "ticker": row.get("ticker", ""), "industry": row.get("industry", ""),
            "priority": priority_bucket(final_score), "risk_level": mock["risk"], "final_score": final_score,
            "pure_financial_rule_score": mock["rule"], "ai_base_score": mock["ai"], "news_trigger_score": news_score,
            "recommended_structure": mock["structure"], "suggested_terms": mock["terms"],
            "rationale": "자동 수집된 공개정보와 정책 v1.1 기준으로 산출된 후보입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
            "key_factors": ["공개정보 기반", "Option C 산식", "뉴스 Trigger 분리", "Risk Gate 별도 적용"],
            "news": classified["key_news"] if classified else [{"title": "테스트 모드: API 키 미설정으로 예시 뉴스 사용", "source": "sample", "sentiment": "mixed", "severity": int(news_score)}],
            "source_status": {"dart": source_status_global["dart"], "news": source_status_global["news"], "rating": "manual_file_needed"},
            "missing_fields": [] if classified else ["live_news_api", "parsed_dart_metrics"]
        })
    issuers = sorted(issuers, key=lambda x: x["final_score"], reverse=True)
    for rank, issuer in enumerate(issuers, 1):
        issuer["rank"] = rank

    return {
        "as_of_date": t.strftime("%Y-%m-%d"),
        "policy_version": "v1.1-static-mvp-option-c",
        "service": {"name": "발행사 선제 영업 플랫폼", "subtitle": "공개정보 기반 자금수요 레이더", "description": "DART, 뉴스, 재무데이터를 기반으로 향후 자금조달 필요성이 높은 발행사를 선제 탐지합니다."},
        "formula": {"label": "Final Funding Score", "rule_weight": 0.45, "ai_weight": 0.40, "news_weight": 0.15, "text": "Final = 45% × Pure Financial Rule + 40% × AI Base + 15% × News Trigger"},
        "pipeline_status": {"last_run_kst": t.strftime("%Y-%m-%d %H:%M:%S"), "next_run_kst": (t + timedelta(days=1)).replace(hour=7, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S"), "source_status": source_status_global, "missing_fields": [], "notice": "공개정보 기반 자동 스냅샷입니다. 투자/영업 실행 전 원문 공시와 담당자 검토가 필요합니다."},
        "kpis": {"screened_universe": len(rows), "priority_a": sum(1 for x in issuers if x["priority"] == "Priority A"), "news_trigger_count": sum(1 for x in issuers if x["news_trigger_score"] >= 60), "needs_review": sum(1 for x in issuers if x["missing_fields"])},
        "filters": {"industries": ["전체"] + sorted({r.get("industry", "") for r in rows if r.get("industry")}), "risk_levels": ["전체", "Low", "Mid", "High"], "priority_levels": ["전체", "Priority A", "Watchlist B", "Monitor C"]},
        "issuers": issuers,
    }


def main() -> None:
    snapshot = build_snapshot()
    OUTPUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(snapshot['issuers'])} issuers")


if __name__ == "__main__":
    main()
