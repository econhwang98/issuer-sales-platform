#!/usr/bin/env python3
"""Generate 발행사 선제 영업 플랫폼 daily_snapshot.json - v1.7 fast full-universe.

Purpose
- Include all OpenDART listed companies in the universe.
- Avoid 1h+ runtimes by using a two-stage pipeline:
  1) Load all listed companies and recent DART disclosures in bulk.
  2) Enrich only likely candidates with Naver News and optional detailed DART financials.

GitHub Actions friendly defaults
- All listed companies are displayed.
- Naver News enrichment: top 150 event/candidate companies only.
- Detailed DART financial API: off by default. Set DART_FINANCIAL_LIMIT to 100~300 only after runtime is stable.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
import zipfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:
    requests = None

KST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parents[1] if Path(__file__).resolve().parent.name == "scripts" else Path(__file__).resolve().parent
UNIVERSE_PATH = ROOT / "universe.csv"
OUTPUT_PATH = ROOT / "daily_snapshot.json"
RAW_DIR = ROOT / "raw_cache"
RAW_DIR.mkdir(exist_ok=True)

NEGATIVE_TERMS = [
    "적자", "부진", "하락", "둔화", "차입", "리파이낸싱", "유동성", "채무", "부도", "자본잠식",
    "등급 하향", "신용등급 하향", "PF", "만기", "손상", "감소", "손실", "상환", "위험", "경고",
]
POSITIVE_TERMS = ["수주", "흑자", "증설", "투자", "성장", "개선", "턴어라운드", "계약", "증가", "승인", "호조"]
EVENT_SEVERITY = {
    "유상증자": 100, "채무불이행": 100, "자본잠식": 100,
    "전환사채": 95, "CB": 95, "신주인수권부사채": 95, "BW": 95,
    "교환사채": 92, "EB": 92,
    "회사채": 90, "사채": 90, "CP": 90, "단기차입금": 90, "차입금 증가": 90,
    "리파이낸싱": 90, "대규모 투자": 90, "CAPEX": 90, "공장 증설": 88, "증설": 85,
    "타법인 주식": 84, "출자증권 취득": 84, "유형자산 취득": 84,
    "신용등급 하향": 80, "등급전망 부정적": 80, "PF": 80,
    "업황 둔화": 60, "원가 상승": 60, "스프레드 축소": 60,
}
DART_EVENT_KEYWORDS = [
    "유상증자", "전환사채", "신주인수권부사채", "교환사채", "회사채", "사채", "단기차입금",
    "채무보증", "담보제공", "타법인", "출자증권", "유형자산", "증권신고서", "주요사항보고서",
]


def now_kst() -> datetime:
    return datetime.now(KST)


def clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def compute_sentiment_pressure(corp_positive: int, corp_negative: int, industry_positive: int = 0, industry_negative: int = 0) -> float:
    return clip(50 + 12 * (corp_negative - corp_positive) + 8 * (industry_negative - industry_positive))


def compute_news_trigger_score(event_severity: Optional[int], corp_positive: int, corp_negative: int, industry_positive: int = 0, industry_negative: int = 0) -> float:
    return round(max(event_severity or 0, compute_sentiment_pressure(corp_positive, corp_negative, industry_positive, industry_negative)), 1)


def compute_final_funding_score(pure_fin_rule_score: float, ai_base_score: float, news_trigger_score: float) -> float:
    return round(0.45 * pure_fin_rule_score + 0.40 * ai_base_score + 0.15 * news_trigger_score, 1)


def priority_bucket(score: float) -> str:
    if score >= 70:
        return "Priority A"
    if score >= 55:
        return "Watchlist B"
    if score >= 40:
        return "Monitor C"
    return "Low Priority"


def ai_base_from_signals(rule_score: float, news_score: float, dart_event_score: int, missing_count: int) -> float:
    score = 0.65 * rule_score + 0.20 * news_score + 0.15 * dart_event_score
    if missing_count >= 5:
        score -= 4
    elif missing_count >= 3:
        score -= 2
    return round(clip(score), 1)


def risk_and_structure(rule_score: float, news_score: float, dart_event_score: int, missing_fields: List[str]) -> Tuple[str, str, str]:
    if len(missing_fields) >= 6:
        return "High", "Other / Manual Review", "원문 공시 확인 후 조건 제시"
    if rule_score >= 75 or news_score >= 90 or dart_event_score >= 90:
        return "Mid", "CP + 담보부 라인 / CB·BW 검토", "6~12M · 담보/코버넌트 포함"
    if rule_score >= 55 or news_score >= 70:
        return "Mid", "회사채/CP 시장 모니터링", "시장성 조달 조건 확인"
    return "Low", "Credit Loan / 운영자금 라인", "1Y · 변동금리 + 약정"


def save_raw(name: str, obj: Any) -> None:
    try:
        safe = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", name)[:120]
        (RAW_DIR / f"{safe}.json").write_text(json.dumps(obj, ensure_ascii=False)[:500000], encoding="utf-8")
    except Exception:
        pass


def _load_seed_universe() -> List[Dict[str, str]]:
    if not UNIVERSE_PATH.exists():
        return []
    with UNIVERSE_PATH.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _fetch_opendart_listed_companies() -> List[Dict[str, str]]:
    if requests is None:
        raise RuntimeError("requests package is not installed")
    key = os.getenv("OPENDART_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENDART_API_KEY GitHub Secret is empty or missing")
    r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"OpenDART corpCode.xml HTTP {r.status_code}: {(r.text or '')[:500]!r}")
    bio = BytesIO(r.content)
    if not zipfile.is_zipfile(bio):
        status = message = None
        try:
            root_err = ET.fromstring(r.content)
            status = root_err.findtext("status")
            message = root_err.findtext("message")
        except Exception:
            pass
        raise RuntimeError(
            "OpenDART corpCode.xml did not return a ZIP. "
            f"dart_status={status!r}, dart_message={message!r}, body_preview={(r.text or '')[:500]!r}"
        )
    bio.seek(0)
    with zipfile.ZipFile(bio) as zf:
        xml_name = zf.namelist()[0]
        root = ET.fromstring(zf.read(xml_name))
    rows: List[Dict[str, str]] = []
    for item in root.findall(".//list"):
        corp_name = (item.findtext("corp_name") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        if corp_name and corp_code and stock_code:
            rows.append({"corp_name": corp_name, "corp_code": corp_code, "ticker": stock_code, "industry": "", "keywords": corp_name})
    print(f"OpenDART listed universe loaded: {len(rows)} rows")
    return rows


def load_universe() -> List[Dict[str, str]]:
    seeds = _load_seed_universe()
    dart_rows = _fetch_opendart_listed_companies()
    seed_by_code = {r.get("corp_code", ""): r for r in seeds if r.get("corp_code")}
    seed_by_ticker = {r.get("ticker", ""): r for r in seeds if r.get("ticker")}
    merged: List[Dict[str, str]] = []
    seen = set()
    def add(row: Dict[str, str]) -> None:
        key = (row.get("corp_code") or row.get("ticker") or row.get("corp_name") or "").strip()
        if not key or key in seen:
            return
        seen.add(key)
        merged.append(row)
    for row in dart_rows:
        manual = seed_by_code.get(row.get("corp_code", "")) or seed_by_ticker.get(row.get("ticker", ""))
        enriched = dict(row)
        if manual:
            enriched["industry"] = manual.get("industry", "") or enriched.get("industry", "")
            enriched["keywords"] = manual.get("keywords", "") or enriched.get("keywords", "")
        add(enriched)
    if len(merged) < 1000:
        raise RuntimeError(f"OpenDART listed universe returned only {len(merged)} rows")
    return merged


def opendart_get(path: str, params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    if requests is None:
        return {"status": "requests_missing", "list": []}
    key = os.getenv("OPENDART_API_KEY", "").strip()
    if not key:
        return {"status": "api_key_missing", "list": []}
    r = requests.get(f"https://opendart.fss.or.kr/api/{path}", params={"crtfc_key": key, **params}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def dart_global_disclosure_search(days: int = 45, max_pages: int = 30) -> List[Dict[str, Any]]:
    """Fetch recent disclosures in bulk. This is much faster than per-company disclosure calls."""
    today = now_kst().date()
    bgn = (today - timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    all_items: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        data = opendart_get("list.json", {
            "bgn_de": bgn,
            "end_de": end,
            "last_reprt_at": "Y",
            "sort": "date",
            "sort_mth": "desc",
            "page_no": str(page),
            "page_count": "100",
        }, timeout=30)
        if data.get("status") not in {"000", "013"}:
            print(f"DART global disclosure status={data.get('status')} message={data.get('message')}")
            break
        items = data.get("list") or []
        if not items:
            break
        all_items.extend(items)
        if len(items) < 100:
            break
        time.sleep(float(os.getenv("API_SLEEP_SECONDS", "0.02")))
    print(f"OpenDART global disclosures loaded: {len(all_items)} rows")
    return all_items


def classify_event_texts(items: List[Dict[str, Any]], source: str) -> Dict[str, Any]:
    max_event = 0
    key_events = []
    for item in items:
        title = strip_html(str(item.get("title") or item.get("report_nm") or item.get("rpt_nm") or ""))
        text = f"{title} {strip_html(str(item.get('description', '')))}"
        severity = 0
        for event, score in EVENT_SEVERITY.items():
            if event in text:
                severity = max(severity, score)
        if severity == 0 and any(k in text for k in DART_EVENT_KEYWORDS):
            severity = 75
        if severity:
            max_event = max(max_event, severity)
            rcept_no = item.get("rcept_no", "")
            url = item.get("url") or (f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "")
            key_events.append({"title": title[:120], "source": source, "sentiment": "event", "severity": severity, "url": url})
    return {"event_severity": max_event, "key_events": sorted(key_events, key=lambda x: x.get("severity", 0), reverse=True)[:5]}


def naver_news_search(query: str, display: int = 3) -> List[Dict[str, Any]]:
    if requests is None:
        return []
    client_id = os.getenv("NAVER_CLIENT_ID", "").strip()
    client_secret = os.getenv("NAVER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return []
    r = requests.get(
        "https://openapi.naver.com/v1/search/news.json",
        headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret},
        params={"query": query, "display": display, "sort": "date"},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("items", [])


def collect_naver_news(row: Dict[str, str]) -> List[Dict[str, Any]]:
    corp = row.get("corp_name", "").strip()
    if not corp:
        return []
    max_queries = int(os.getenv("MAX_NEWS_QUERIES_PER_ISSUER", "2"))
    display = int(os.getenv("NAVER_NEWS_DISPLAY", "3"))
    queries = [corp, f"{corp} 유상증자 전환사채 회사채 차입 유동성 신용등급"][:max_queries]
    items: List[Dict[str, Any]] = []
    seen = set()
    for q in queries:
        try:
            for item in naver_news_search(q, display=display):
                key = item.get("originallink") or item.get("link") or item.get("title")
                if key in seen:
                    continue
                seen.add(key)
                item["query"] = q
                items.append(item)
            time.sleep(float(os.getenv("API_SLEEP_SECONDS", "0.02")))
        except Exception as exc:
            print(f"NAVER API error query={q}: {type(exc).__name__}: {exc}")
            break
    return items[:20]


def classify_news(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    corp_pos = corp_neg = 0
    max_event = 0
    key_news = []
    seen = set()
    for item in items[:20]:
        title = strip_html(item.get("title", ""))
        desc = strip_html(item.get("description", ""))
        if title in seen:
            continue
        seen.add(title)
        text = f"{title} {desc}"
        neg = sum(1 for w in NEGATIVE_TERMS if w in text)
        pos = sum(1 for w in POSITIVE_TERMS if w in text)
        corp_neg += 1 if neg > pos else 0
        corp_pos += 1 if pos > neg else 0
        event_hit = 0
        for event, severity in EVENT_SEVERITY.items():
            if event in text:
                event_hit = max(event_hit, severity)
        max_event = max(max_event, event_hit)
        if neg or pos or event_hit:
            key_news.append({
                "title": title[:120],
                "source": "naver_news",
                "sentiment": "negative" if neg > pos else "positive" if pos > neg else "mixed",
                "severity": event_hit or 50,
                "query": item.get("query", ""),
                "url": item.get("originallink") or item.get("link") or "",
            })
    trigger = compute_news_trigger_score(max_event or None, corp_pos, corp_neg)
    return {"score": trigger, "corp_positive": corp_pos, "corp_negative": corp_neg, "event_severity": max_event, "key_news": sorted(key_news, key=lambda x: x.get("severity", 0), reverse=True)[:5]}


def fetch_kind_rss() -> List[Dict[str, Any]]:
    url = os.getenv("KRX_KIND_RSS_URL", "").strip()
    if not url or requests is None:
        return []
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:500]:
        items.append({"title": item.findtext("title") or "", "description": item.findtext("description") or "", "url": item.findtext("link") or ""})
    return items


def filter_items_for_company(items: List[Dict[str, Any]], corp_name: str) -> List[Dict[str, Any]]:
    if not items or not corp_name:
        return []
    return [x for x in items if corp_name in f"{x.get('title','')} {x.get('description','')}"][:10]


def mock_rule_score(idx: int, event_severity: int) -> float:
    # Until per-company financial detail is enabled, keep a conservative base score.
    base_cycle = [42.0, 45.0, 48.0, 51.0, 54.0]
    return min(75.0, base_cycle[idx % len(base_cycle)] + (10 if event_severity >= 90 else 5 if event_severity >= 75 else 0))


def build_snapshot() -> Dict[str, Any]:
    t = now_kst()
    rows = load_universe()
    source_status_global = {
        "dart_universe": "live_ok",
        "dart_disclosure": "api_key_missing",
        "dart_financial": "fast_mode_deferred",
        "news": "api_key_missing",
        "kind_rss": "not_configured",
        "fsc_profile": "reserved_hook_not_enabled",
        "credit_rating": "manual_file_needed",
        "llm_annotation": "transparent_proxy_static_mvp",
    }

    disclosure_by_code: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    try:
        max_pages = int(os.getenv("DART_DISCLOSURE_MAX_PAGES", "30"))
        days = int(os.getenv("DART_DISCLOSURE_LOOKBACK_DAYS", "45"))
        global_disc = dart_global_disclosure_search(days=days, max_pages=max_pages)
        save_raw("dart_global_disclosures", global_disc[:300])
        for item in global_disc:
            code = str(item.get("corp_code") or "").strip()
            if code:
                disclosure_by_code[code].append(item)
        source_status_global["dart_disclosure"] = "live_ok"
    except Exception as exc:
        source_status_global["dart_disclosure"] = f"error:{type(exc).__name__}"
        print(f"DART global disclosure error: {type(exc).__name__}: {exc}")

    kind_items: List[Dict[str, Any]] = []
    if os.getenv("KRX_KIND_RSS_URL"):
        try:
            kind_items = fetch_kind_rss()
            source_status_global["kind_rss"] = "live_ok"
        except Exception as exc:
            source_status_global["kind_rss"] = f"error:{type(exc).__name__}"

    # Stage 1: all listed companies, event-driven scoring only.
    issuers: List[Dict[str, Any]] = []
    for i, row in enumerate(rows):
        corp_name = row.get("corp_name", "")
        corp_code = row.get("corp_code", "")
        dart_events = classify_event_texts(disclosure_by_code.get(corp_code, []), "opendart_disclosure")
        kind_events = classify_event_texts(filter_items_for_company(kind_items, corp_name), "kind_rss") if kind_items else {"event_severity": 0, "key_events": []}
        event_severity = max(int(dart_events.get("event_severity", 0)), int(kind_events.get("event_severity", 0)))
        rule_score = mock_rule_score(i, event_severity)
        news_score = float(max(50 if event_severity == 0 else event_severity, event_severity))
        missing_fields = ["detailed_dart_financial_deferred"]
        ai_score = ai_base_from_signals(rule_score, news_score, event_severity, len(missing_fields))
        final_score = compute_final_funding_score(rule_score, ai_score, news_score)
        news_cards = []
        news_cards.extend(dart_events.get("key_events", []))
        news_cards.extend(kind_events.get("key_events", []))
        if not news_cards:
            news_cards = [{"title": "최근 45일 주요 자금조달 이벤트 공시 미검출", "source": "full_universe_fast_screen", "sentiment": "mixed", "severity": int(news_score)}]
        risk, structure, terms = risk_and_structure(rule_score, news_score, event_severity, missing_fields)
        issuers.append({
            "rank": 0,
            "corp_name": corp_name,
            "corp_code": corp_code,
            "ticker": row.get("ticker", ""),
            "industry": row.get("industry", ""),
            "priority": priority_bucket(final_score),
            "risk_level": risk,
            "final_score": final_score,
            "pure_financial_rule_score": round(rule_score, 1),
            "ai_base_score": ai_score,
            "news_trigger_score": round(news_score, 1),
            "recommended_structure": structure,
            "suggested_terms": terms,
            "rationale": "전체 상장사 Fast Scan 결과입니다. 최근 이벤트 공시를 우선 반영하고, 상위 후보에만 뉴스/상세 재무 보강을 수행합니다.",
            "key_factors": ["전체 상장사 포함", "DART 이벤트 공시 Bulk Scan", "뉴스 Trigger 분리", "Risk Gate 별도 적용"],
            "news": sorted(news_cards, key=lambda x: x.get("severity", 0), reverse=True)[:5],
            "source_status": {
                "dart_universe": source_status_global["dart_universe"],
                "dart_financial": source_status_global["dart_financial"],
                "dart_disclosure": source_status_global["dart_disclosure"],
                "news": source_status_global["news"],
                "kind_rss": source_status_global["kind_rss"],
                "rating": "manual_file_needed",
            },
            "missing_fields": missing_fields,
            "rule_breakdown": [],
        })

    issuers = sorted(issuers, key=lambda x: x["final_score"], reverse=True)

    # Stage 2: Naver News enrichment only for top candidates.
    news_limit = int(os.getenv("NEWS_ENRICH_LIMIT", "150"))
    if os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET") and news_limit > 0:
        source_status_global["news"] = "live_partial_top_candidates"
        for issuer in issuers[:news_limit]:
            try:
                news_items = collect_naver_news(issuer)
                classified = classify_news(news_items)
                n_event = int(classified.get("event_severity", 0) or 0)
                existing_event = max([int(x.get("severity", 0) or 0) for x in issuer.get("news", [])] + [0])
                event_severity = max(existing_event, n_event)
                news_score = max(float(classified.get("score", 0) or 0), float(event_severity or 0), issuer["news_trigger_score"])
                rule_score = issuer["pure_financial_rule_score"]
                ai_score = ai_base_from_signals(rule_score, news_score, event_severity, len(issuer.get("missing_fields", [])))
                final_score = compute_final_funding_score(rule_score, ai_score, news_score)
                issuer.update({
                    "news_trigger_score": round(news_score, 1),
                    "ai_base_score": ai_score,
                    "final_score": final_score,
                    "priority": priority_bucket(final_score),
                    "source_status": {**issuer["source_status"], "news": "live_ok_top_candidate"},
                })
                merged_news = classified.get("key_news", []) + issuer.get("news", [])
                issuer["news"] = sorted(merged_news, key=lambda x: x.get("severity", 0), reverse=True)[:5]
            except Exception as exc:
                source_status_global["news"] = f"partial_error:{type(exc).__name__}"
                issuer["source_status"] = {**issuer["source_status"], "news": f"error:{type(exc).__name__}"}
                print(f"Naver enrich error for {issuer.get('corp_name')}: {type(exc).__name__}: {exc}")
                break

    issuers = sorted(issuers, key=lambda x: x["final_score"], reverse=True)
    page_detail_limit = int(os.getenv("PAGE_DETAIL_LIMIT", "300"))
    for rank, issuer in enumerate(issuers, 1):
        issuer["rank"] = rank
        if page_detail_limit > 0 and rank > page_detail_limit:
            issuer["news"] = [{"title": f"전체 상장사 스크리닝 대상입니다. 상세 뉴스/공시 근거는 상위 {page_detail_limit}개 우선순위 기업 중심으로 저장됩니다.", "source": "full_universe_summary", "sentiment": "mixed", "severity": int(issuer.get("news_trigger_score", 0) or 0)}]
            issuer["rule_breakdown"] = []
            issuer["rationale"] = "전체 상장사 Fast Scan 요약입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다."

    next_run = (t + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return {
        "as_of_date": t.strftime("%Y-%m-%d"),
        "policy_version": "v1.7-fast-full-universe-two-stage",
        "service": {
            "name": "발행사 선제 영업 플랫폼",
            "subtitle": "공개정보 기반 자금수요 레이더",
            "description": "OpenDART 전체 상장사와 최근 이벤트 공시를 Bulk Scan하고, 상위 후보에 뉴스 Trigger를 보강합니다.",
        },
        "formula": {
            "label": "Final Funding Score",
            "rule_weight": 0.45,
            "ai_weight": 0.40,
            "news_weight": 0.15,
            "text": "Final = 45% × Pure Financial Rule + 40% × AI Base + 15% × News Trigger",
        },
        "pipeline_status": {
            "last_run_kst": t.strftime("%Y-%m-%d %H:%M:%S"),
            "next_run_kst": next_run.strftime("%Y-%m-%d %H:%M:%S"),
            "scheduled_run": "매일 08:00 KST / GitHub Actions cron 0 23 * * * UTC",
            "source_status": source_status_global,
            "universe_count": len(rows),
            "universe_mode": "opendart_fast_full_listed_two_stage",
            "news_enrich_limit": news_limit,
            "page_detail_limit": page_detail_limit,
            "missing_fields": [],
            "notice": "공개정보 기반 자동 스냅샷입니다. 투자/영업 실행 전 원문 공시와 담당자 검토가 필요합니다.",
        },
        "kpis": {
            "screened_universe": len(rows),
            "priority_a": sum(1 for x in issuers if x["priority"] == "Priority A"),
            "news_trigger_count": sum(1 for x in issuers if x["news_trigger_score"] >= 60),
            "needs_review": sum(1 for x in issuers if x["missing_fields"]),
        },
        "filters": {
            "industries": ["전체"] + sorted({r.get("industry", "") for r in rows if r.get("industry")}),
            "risk_levels": ["전체", "Low", "Mid", "High"],
            "priority_levels": ["전체", "Priority A", "Watchlist B", "Monitor C", "Low Priority"],
        },
        "issuers": issuers,
    }


def main() -> None:
    snapshot = build_snapshot()
    OUTPUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(snapshot['issuers'])} issuers")


if __name__ == "__main__":
    main()
