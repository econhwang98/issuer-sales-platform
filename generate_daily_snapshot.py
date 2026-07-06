#!/usr/bin/env python3
"""Generate 발행사 선제 영업 플랫폼 daily_snapshot.json.

Static GitHub Pages MVP runtime model:
- This script runs in GitHub Actions once daily at 08:00 KST.
- API keys stay in GitHub Actions Secrets only.
- index.html reads only the generated daily_snapshot.json.

Required for live mode:
- OPENDART_API_KEY
- NAVER_CLIENT_ID
- NAVER_CLIENT_SECRET

Optional enrichment:
- KRX_KIND_RSS_URL      # KIND RSS URL copied from KIND page, if you want KRX/KIND backup signal
- FSC_SERVICE_KEY       # reserved hook for data.go.kr FSC corporate/financial APIs

Without required API keys, it produces a deterministic sample snapshot so the page remains testable.
"""
from __future__ import annotations

import csv
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests
except ImportError:  # allows linting without deps
    requests = None


from dataclasses import dataclass, field


def clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


@dataclass
class FinancialMetrics:
    debt_to_equity_pct: Optional[float] = None
    current_ratio_pct: Optional[float] = None
    quick_ratio_pct: Optional[float] = None
    debt_dependence_pct: Optional[float] = None
    interest_coverage: Optional[float] = None
    operating_cash_flow: Optional[float] = None
    investing_cash_flow: Optional[float] = None
    net_cash_change: Optional[float] = None


@dataclass
class RuleScoreResult:
    score: float
    raw_points: int
    available_max_points: int
    breakdown: List[Dict[str, Any]]
    missing_fields: List[str] = field(default_factory=list)


def compute_pure_financial_rule_score(m: FinancialMetrics) -> RuleScoreResult:
    breakdown: List[Dict[str, Any]] = []
    missing: List[str] = []
    raw = 0
    available_max = 0

    def add(factor: str, points: int, value: Any, available: bool = True):
        nonlocal raw, available_max
        if not available:
            missing.append(factor)
            breakdown.append({"factor": factor, "value": None, "points": 0, "status": "not_available"})
            return
        raw += points
        available_max += 100
        breakdown.append({"factor": factor, "value": value, "points": points, "status": "applied"})

    if m.debt_to_equity_pct is None: add("부채비율", 0, None, False)
    elif m.debt_to_equity_pct >= 200: add("부채비율", 100, m.debt_to_equity_pct)
    elif m.debt_to_equity_pct >= 150: add("부채비율", 75, m.debt_to_equity_pct)
    elif m.debt_to_equity_pct >= 100: add("부채비율", 50, m.debt_to_equity_pct)
    else: add("부채비율", 0, m.debt_to_equity_pct)

    if m.current_ratio_pct is None: add("유동비율", 0, None, False)
    elif m.current_ratio_pct < 100: add("유동비율", 100, m.current_ratio_pct)
    elif m.current_ratio_pct < 150: add("유동비율", 75, m.current_ratio_pct)
    elif m.current_ratio_pct < 200: add("유동비율", 50, m.current_ratio_pct)
    else: add("유동비율", 0, m.current_ratio_pct)

    # 사용자 확정: 당좌비율 3단계 기준은 10% 미만이 아니라 100% 미만.
    if m.quick_ratio_pct is None: add("당좌비율", 0, None, False)
    elif m.quick_ratio_pct < 100: add("당좌비율", 100, m.quick_ratio_pct)
    elif m.quick_ratio_pct < 150: add("당좌비율", 75, m.quick_ratio_pct)
    elif m.quick_ratio_pct < 200: add("당좌비율", 50, m.quick_ratio_pct)
    else: add("당좌비율", 0, m.quick_ratio_pct)

    if m.debt_dependence_pct is None: add("차입금의존도", 0, None, False)
    elif m.debt_dependence_pct >= 60: add("차입금의존도", 100, m.debt_dependence_pct)
    elif m.debt_dependence_pct >= 30: add("차입금의존도", 50, m.debt_dependence_pct)
    else: add("차입금의존도", 0, m.debt_dependence_pct)

    if m.interest_coverage is None: add("이자보상배율", 0, None, False)
    elif m.interest_coverage < 1: add("이자보상배율", 100, m.interest_coverage)
    elif m.interest_coverage < 1.5: add("이자보상배율", 50, m.interest_coverage)
    else: add("이자보상배율", 0, m.interest_coverage)

    if m.operating_cash_flow is None: add("영업활동현금흐름", 0, None, False)
    else: add("영업활동현금흐름", 100 if m.operating_cash_flow < 0 else 0, m.operating_cash_flow)

    if m.operating_cash_flow is None or m.investing_cash_flow is None: add("OCF 양수 & ICF 음수", 0, None, False)
    else: add("OCF 양수 & ICF 음수", 100 if (m.operating_cash_flow > 0 and m.investing_cash_flow < 0) else 0, {"ocf": m.operating_cash_flow, "icf": m.investing_cash_flow})

    if m.net_cash_change is None: add("현금 순감소", 0, None, False)
    else: add("현금 순감소", 100 if m.net_cash_change < 0 else 0, m.net_cash_change)

    score = round((raw / available_max * 100) if available_max else 0, 1)
    return RuleScoreResult(score=score, raw_points=raw, available_max_points=available_max, breakdown=breakdown, missing_fields=missing)


def compute_sentiment_pressure(corp_positive: int, corp_negative: int, industry_positive: int, industry_negative: int) -> float:
    return clip(50 + 12 * (corp_negative - corp_positive) + 8 * (industry_negative - industry_positive))


def compute_news_trigger_score(event_severity: Optional[int], corp_positive: int, corp_negative: int, industry_positive: int, industry_negative: int) -> float:
    event = event_severity or 0
    pressure = compute_sentiment_pressure(corp_positive, corp_negative, industry_positive, industry_negative)
    return round(max(event, pressure), 1)


def compute_final_funding_score(pure_fin_rule_score: float, ai_base_score: float, news_trigger_score: float) -> float:
    return round(0.45 * pure_fin_rule_score + 0.40 * ai_base_score + 0.15 * news_trigger_score, 1)


def priority_bucket(score: float) -> str:
    if score >= 70: return "Priority A"
    if score >= 55: return "Watchlist B"
    if score >= 40: return "Monitor C"
    return "Low Priority"

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

# Events that directly/indirectly imply funding need or financing opportunity.
EVENT_SEVERITY = {
    "유상증자": 100,
    "채무불이행": 100,
    "자본잠식": 100,
    "전환사채": 95,
    "CB": 95,
    "신주인수권부사채": 95,
    "BW": 95,
    "교환사채": 92,
    "EB": 92,
    "회사채": 90,
    "사채": 90,
    "CP": 90,
    "단기차입금": 90,
    "차입금 증가": 90,
    "리파이낸싱": 90,
    "대규모 투자": 90,
    "CAPEX": 90,
    "공장 증설": 88,
    "증설": 85,
    "타법인 주식": 84,
    "출자증권 취득": 84,
    "유형자산 취득": 84,
    "신용등급 하향": 80,
    "등급전망 부정적": 80,
    "PF": 80,
    "업황 둔화": 60,
    "원가 상승": 60,
    "스프레드 축소": 60,
}

DART_EVENT_KEYWORDS = [
    "유상증자", "전환사채", "신주인수권부사채", "교환사채", "회사채", "사채", "단기차입금",
    "채무보증", "담보제공", "타법인", "출자증권", "유형자산", "증권신고서", "주요사항보고서",
]

NAVER_EVENT_QUERIES = [
    "유상증자", "CB", "전환사채", "BW", "신주인수권부사채", "회사채", "CP", "차입", "리파이낸싱",
    "공장 증설", "CAPEX", "대규모 투자", "신용등급", "유동성", "자본잠식", "적자", "PF",
]
INDUSTRY_QUERIES = ["업황 둔화", "스프레드 축소", "원가 상승", "수요 부진", "재고 부담", "금리 부담"]


def now_kst() -> datetime:
    return datetime.now(KST)


def load_universe() -> List[Dict[str, str]]:
    with UNIVERSE_PATH.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save_raw(name: str, obj: Any) -> None:
    """Persist a small raw/cache artifact for later troubleshooting.

    GitHub Actions commits only daily_snapshot.json by default, so this is mainly local/debug cache.
    """
    try:
        safe = re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", name)[:140]
        (RAW_DIR / f"{safe}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    return text.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def to_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s or s in {"-", "--"}:
        return None
    # DART sometimes uses parentheses for negative values.
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        n = float(s)
        return -n if neg else n
    except ValueError:
        return None


def find_account_amount(rows: List[Dict[str, Any]], patterns: List[str], sj_div: Optional[str] = None) -> Optional[float]:
    candidates = []
    for row in rows:
        account = str(row.get("account_nm", ""))
        if sj_div and str(row.get("sj_div", "")) != sj_div:
            continue
        if any(p in account for p in patterns):
            amt = to_number(row.get("thstrm_amount") or row.get("thstrm_add_amount"))
            if amt is not None:
                candidates.append((len(account), amt))
    if not candidates:
        return None
    # Prefer the shortest matching account name to avoid over-specific child accounts.
    return sorted(candidates, key=lambda x: x[0])[0][1]


def sum_account_amounts(rows: List[Dict[str, Any]], patterns: List[str], sj_div: Optional[str] = None) -> Optional[float]:
    total = 0.0
    hit = False
    seen = set()
    for row in rows:
        account = str(row.get("account_nm", ""))
        if sj_div and str(row.get("sj_div", "")) != sj_div:
            continue
        if any(p in account for p in patterns):
            key = (account, row.get("thstrm_amount"), row.get("sj_div"))
            if key in seen:
                continue
            seen.add(key)
            amt = to_number(row.get("thstrm_amount") or row.get("thstrm_add_amount"))
            if amt is not None:
                total += amt
                hit = True
    return total if hit else None


def parse_financial_metrics(dart_rows: List[Dict[str, Any]]) -> Tuple[FinancialMetrics, List[str]]:
    """Best-effort parse of common Korean IFRS account names.

    DART account naming differs by issuer, so missing_fields are intentionally surfaced.
    """
    assets = find_account_amount(dart_rows, ["자산총계", "자산 총계"], "BS")
    liabilities = find_account_amount(dart_rows, ["부채총계", "부채 총계"], "BS")
    current_assets = find_account_amount(dart_rows, ["유동자산"], "BS")
    current_liabilities = find_account_amount(dart_rows, ["유동부채"], "BS")
    inventories = find_account_amount(dart_rows, ["재고자산"], "BS") or 0
    operating_income = find_account_amount(dart_rows, ["영업이익", "영업손실"], "IS")
    interest_expense = find_account_amount(dart_rows, ["이자비용", "금융비용"], "IS")
    ocf = find_account_amount(dart_rows, ["영업활동현금흐름", "영업활동 현금흐름"], "CF")
    icf = find_account_amount(dart_rows, ["투자활동현금흐름", "투자활동 현금흐름"], "CF")
    net_cash_change = find_account_amount(dart_rows, ["현금및현금성자산의순증가", "현금및현금성자산의 증가", "현금의 증가", "현금및현금성자산의 감소"], "CF")
    borrowings = sum_account_amounts(dart_rows, ["단기차입", "장기차입", "유동성장기", "사채", "리스부채"], "BS")

    missing = []
    if assets is None: missing.append("자산총계")
    if liabilities is None: missing.append("부채총계")
    if current_assets is None: missing.append("유동자산")
    if current_liabilities in (None, 0): missing.append("유동부채")
    if operating_income is None: missing.append("영업이익")
    if interest_expense in (None, 0): missing.append("이자비용")
    if ocf is None: missing.append("영업활동현금흐름")
    if icf is None: missing.append("투자활동현금흐름")
    if net_cash_change is None: missing.append("현금순증감")

    debt_to_equity = None
    if assets is not None and liabilities is not None and assets - liabilities:
        debt_to_equity = liabilities / (assets - liabilities) * 100

    current_ratio = None
    quick_ratio = None
    if current_assets is not None and current_liabilities not in (None, 0):
        current_ratio = current_assets / current_liabilities * 100
        quick_ratio = (current_assets - inventories) / current_liabilities * 100

    debt_dependence = None
    if borrowings is not None and assets not in (None, 0):
        debt_dependence = borrowings / assets * 100
    elif borrowings is None:
        missing.append("차입금/사채")

    interest_coverage = None
    if operating_income is not None and interest_expense not in (None, 0):
        # Some companies show interest expense as negative. Use absolute denominator.
        interest_coverage = operating_income / abs(interest_expense)

    return FinancialMetrics(
        debt_to_equity_pct=round(debt_to_equity, 1) if debt_to_equity is not None else None,
        current_ratio_pct=round(current_ratio, 1) if current_ratio is not None else None,
        quick_ratio_pct=round(quick_ratio, 1) if quick_ratio is not None else None,
        debt_dependence_pct=round(debt_dependence, 1) if debt_dependence is not None else None,
        interest_coverage=round(interest_coverage, 2) if interest_coverage is not None else None,
        operating_cash_flow=ocf,
        investing_cash_flow=icf,
        net_cash_change=net_cash_change,
    ), sorted(set(missing))


def opendart_get(path: str, params: Dict[str, Any], timeout: int = 30) -> Dict[str, Any]:
    if requests is None:
        return {"status": "requests_missing", "list": []}
    key = os.getenv("OPENDART_API_KEY")
    if not key:
        return {"status": "api_key_missing", "list": []}
    url = f"https://opendart.fss.or.kr/api/{path}"
    q = {"crtfc_key": key, **params}
    r = requests.get(url, params=q, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data


def dart_single_company_all_accounts(corp_code: str, year: int, reprt_code: str = "11011", fs_div: str = "CFS") -> Dict[str, Any]:
    return opendart_get("fnlttSinglAcntAll.json", {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div})


def dart_disclosure_search(corp_code: str, days: int = 45) -> Dict[str, Any]:
    today = now_kst().date()
    bgn = (today - timedelta(days=days)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    return opendart_get("list.json", {
        "corp_code": corp_code,
        "bgn_de": bgn,
        "end_de": end,
        "last_reprt_at": "Y",
        "sort": "date",
        "sort_mth": "desc",
        "page_no": "1",
        "page_count": "30",
    }, timeout=20)


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
            key_events.append({
                "title": title[:120],
                "source": source,
                "sentiment": "event",
                "severity": severity,
                "url": item.get("url") or item.get("rcept_url") or "",
            })
    return {"event_severity": max_event, "key_events": key_events[:5]}


def naver_news_search(query: str, display: int = 10) -> List[Dict[str, Any]]:
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


def build_news_queries(row: Dict[str, str]) -> List[str]:
    corp = row.get("corp_name", "").strip()
    industry = row.get("industry", "").strip()
    base_keywords = row.get("keywords", "").strip()
    queries = []
    if base_keywords:
        queries.append(base_keywords)
    if corp:
        queries.append(corp)
        for term in NAVER_EVENT_QUERIES[:8]:
            queries.append(f"{corp} {term}")
    if industry:
        for term in INDUSTRY_QUERIES[:3]:
            queries.append(f"{industry} {term}")
    # de-duplicate while preserving order, cap to protect API quota.
    out = []
    seen = set()
    for q in queries:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out[:8]


def collect_naver_news(row: Dict[str, str]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen_links = set()
    for q in build_news_queries(row):
        try:
            for item in naver_news_search(q, display=10):
                link = item.get("originallink") or item.get("link") or item.get("title")
                if link in seen_links:
                    continue
                seen_links.add(link)
                item["query"] = q
                items.append(item)
            time.sleep(0.12)
        except Exception as exc:
            items.append({"title": f"NAVER API error for query={q}: {type(exc).__name__}", "description": "", "error": True})
            break
    return items[:60]


def classify_news(items: List[Dict[str, Any]], corp_name: str = "", industry: str = "") -> Dict[str, Any]:
    corp_pos = corp_neg = ind_pos = ind_neg = 0
    max_event = 0
    key_news = []
    seen = set()
    for item in items[:60]:
        if item.get("error"):
            continue
        title = strip_html(item.get("title", ""))
        desc = strip_html(item.get("description", ""))
        text = f"{title} {desc}"
        if title in seen:
            continue
        seen.add(title)
        neg = sum(1 for w in NEGATIVE_TERMS if w in text)
        pos = sum(1 for w in POSITIVE_TERMS if w in text)
        is_industry = bool(industry and industry in str(item.get("query", "")))
        if is_industry:
            ind_neg += 1 if neg > pos else 0
            ind_pos += 1 if pos > neg else 0
        else:
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
    trigger = compute_news_trigger_score(max_event or None, corp_pos, corp_neg, ind_pos, ind_neg)
    return {
        "score": trigger,
        "corp_positive": corp_pos,
        "corp_negative": corp_neg,
        "industry_positive": ind_pos,
        "industry_negative": ind_neg,
        "event_severity": max_event,
        "key_news": sorted(key_news, key=lambda x: x.get("severity", 0), reverse=True)[:5],
    }


def fetch_kind_rss() -> List[Dict[str, Any]]:
    """Optional KIND RSS enrichment. Paste a KIND RSS URL into KRX_KIND_RSS_URL secret/env.

    The public KIND web screen provides RSS links; exact query can be managed by the user at KIND.
    """
    url = os.getenv("KRX_KIND_RSS_URL", "").strip()
    if not url or requests is None:
        return []
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    items = []
    for item in root.findall(".//item")[:200]:
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        desc = item.findtext("description") or ""
        items.append({"title": title, "description": desc, "url": link})
    return items


def filter_kind_items_for_company(kind_items: List[Dict[str, Any]], corp_name: str) -> List[Dict[str, Any]]:
    if not kind_items or not corp_name:
        return []
    return [x for x in kind_items if corp_name in f"{x.get('title','')} {x.get('description','')}"][:10]


def ai_base_from_signals(rule_score: float, news_score: float, dart_event_score: int, missing_count: int) -> float:
    """Transparent proxy for AI Base in static MVP.

    In a production backend this can be replaced with an LLM annotation step reading DART notes,
    MD&A, recent filings, and news evidence. Static Pages should not call an LLM directly.
    """
    score = 0.65 * rule_score + 0.20 * news_score + 0.15 * dart_event_score
    if missing_count >= 5:
        score -= 4
    elif missing_count >= 3:
        score -= 2
    return round(max(0, min(100, score)), 1)


def mock_scores_for(row: Dict[str, str], idx: int) -> Dict[str, Any]:
    samples = [
        (78.0, 84.0, 92.0, "Mid", "CP + 담보부 라인", "9M · 6.40% 내외"),
        (66.0, 79.0, 88.0, "Low", "Credit Loan / 운영자금 라인", "1Y · 변동금리 + 약정"),
        (61.0, 65.0, 67.0, "Mid", "만기/금리 모니터링 후 제안", "Review after next filing"),
        (58.0, 64.0, 68.0, "Mid", "회사채/CP 시장 모니터링", "시장성 조달 조건 확인"),
        (54.0, 50.0, 62.0, "High", "Other / Manual Review", "투자심의 전 별도 검토"),
    ]
    return dict(zip(["rule", "ai", "news", "risk", "structure", "terms"], samples[idx % len(samples)]))


def risk_and_structure(rule_score: float, news_score: float, dart_event_score: int, missing_fields: List[str]) -> Tuple[str, str, str]:
    if len(missing_fields) >= 6:
        return "High", "Other / Manual Review", "원문 공시 확인 후 조건 제시"
    if rule_score >= 75 or news_score >= 90 or dart_event_score >= 90:
        return "Mid", "CP + 담보부 라인 / CB·BW 검토", "6~12M · 담보/코버넌트 포함"
    if rule_score >= 55 or news_score >= 70:
        return "Mid", "회사채/CP 시장 모니터링", "시장성 조달 조건 확인"
    return "Low", "Credit Loan / 운영자금 라인", "1Y · 변동금리 + 약정"


def build_snapshot() -> Dict[str, Any]:
    t = now_kst()
    rows = load_universe()
    issuers = []
    source_status_global = {
        "dart_financial": "api_key_missing",
        "dart_disclosure": "api_key_missing",
        "news": "api_key_missing",
        "kind_rss": "not_configured",
        "fsc_profile": "reserved_hook_not_enabled",
        "credit_rating": "manual_file_needed",
        "llm_annotation": "transparent_proxy_static_mvp",
    }

    kind_items: List[Dict[str, Any]] = []
    if os.getenv("KRX_KIND_RSS_URL"):
        try:
            kind_items = fetch_kind_rss()
            source_status_global["kind_rss"] = "live_ok"
        except Exception as exc:
            source_status_global["kind_rss"] = f"error:{type(exc).__name__}"

    current_year = t.year - 1  # Most recent annual filing base. Can be expanded to quarters later.
    for i, row in enumerate(rows):
        corp_name = row.get("corp_name", "")
        corp_code = row.get("corp_code", "")
        industry = row.get("industry", "")
        mock = mock_scores_for(row, i)

        dart_rows: List[Dict[str, Any]] = []
        dart_fin_missing: List[str] = []
        dart_rule_score: Optional[float] = None
        rule_breakdown: List[Dict[str, Any]] = []
        if os.getenv("OPENDART_API_KEY") and corp_code:
            try:
                fin = dart_single_company_all_accounts(corp_code, current_year, reprt_code="11011", fs_div="CFS")
                save_raw(f"dart_financial_{corp_code}_{current_year}", fin)
                if fin.get("status") == "000" and isinstance(fin.get("list"), list):
                    dart_rows = fin.get("list", [])
                    metrics, parsed_missing = parse_financial_metrics(dart_rows)
                    rule_result = compute_pure_financial_rule_score(metrics)
                    dart_rule_score = rule_result.score
                    dart_fin_missing = parsed_missing + rule_result.missing_fields
                    rule_breakdown = rule_result.breakdown
                    source_status_global["dart_financial"] = "live_ok"
                else:
                    dart_fin_missing = [f"dart_status_{fin.get('status')}"]
                    source_status_global["dart_financial"] = f"dart_status_{fin.get('status')}"
            except Exception as exc:
                dart_fin_missing = [f"dart_financial_error:{type(exc).__name__}"]
                source_status_global["dart_financial"] = f"error:{type(exc).__name__}"

        dart_events: Dict[str, Any] = {"event_severity": 0, "key_events": []}
        if os.getenv("OPENDART_API_KEY") and corp_code:
            try:
                disc = dart_disclosure_search(corp_code, days=45)
                save_raw(f"dart_disclosure_{corp_code}", disc)
                if disc.get("status") == "000" and isinstance(disc.get("list"), list):
                    dart_events = classify_event_texts(disc.get("list", []), "opendart_disclosure")
                    source_status_global["dart_disclosure"] = "live_ok"
                else:
                    source_status_global["dart_disclosure"] = f"dart_status_{disc.get('status')}"
            except Exception as exc:
                source_status_global["dart_disclosure"] = f"error:{type(exc).__name__}"

        news_items: List[Dict[str, Any]] = []
        classified_news: Optional[Dict[str, Any]] = None
        if os.getenv("NAVER_CLIENT_ID") and os.getenv("NAVER_CLIENT_SECRET"):
            try:
                news_items = collect_naver_news(row)
                save_raw(f"naver_news_{corp_code or corp_name}", news_items[:20])
                classified_news = classify_news(news_items, corp_name=corp_name, industry=industry)
                source_status_global["news"] = "live_ok"
            except Exception as exc:
                source_status_global["news"] = f"error:{type(exc).__name__}"

        kind_events = classify_event_texts(filter_kind_items_for_company(kind_items, corp_name), "kind_rss") if kind_items else {"event_severity": 0, "key_events": []}
        event_severity = max(int(dart_events.get("event_severity", 0)), int(kind_events.get("event_severity", 0)), int((classified_news or {}).get("event_severity", 0) or 0))

        rule_score = dart_rule_score if dart_rule_score is not None and dart_rule_score > 0 else mock["rule"]
        news_score = max(float((classified_news or {}).get("score", 0) or 0), float(mock["news"] if not classified_news else 0), float(event_severity or 0))
        missing_fields = sorted(set(dart_fin_missing + ([] if classified_news else ["live_news_api"] if not os.getenv("NAVER_CLIENT_ID") else [])))
        if dart_rule_score is None:
            missing_fields.append("parsed_dart_metrics")
        ai_score = ai_base_from_signals(rule_score, news_score, event_severity, len(missing_fields))
        risk, structure, terms = risk_and_structure(rule_score, news_score, event_severity, missing_fields)
        final_score = compute_final_funding_score(rule_score, ai_score, news_score)

        news_cards = []
        if classified_news:
            news_cards.extend(classified_news.get("key_news", []))
        news_cards.extend(dart_events.get("key_events", []))
        news_cards.extend(kind_events.get("key_events", []))
        if not news_cards:
            news_cards = [{"title": "테스트 모드 또는 관련 뉴스/공시 미검출", "source": "sample", "sentiment": "mixed", "severity": int(news_score)}]

        issuers.append({
            "rank": 0,
            "corp_name": corp_name,
            "corp_code": corp_code,
            "ticker": row.get("ticker", ""),
            "industry": industry,
            "priority": priority_bucket(final_score),
            "risk_level": risk,
            "final_score": final_score,
            "pure_financial_rule_score": round(rule_score, 1),
            "ai_base_score": ai_score,
            "news_trigger_score": round(news_score, 1),
            "recommended_structure": structure,
            "suggested_terms": terms,
            "rationale": "DART 재무/이벤트 공시, 네이버 뉴스, 선택형 KIND RSS를 분리 수집한 뒤 Option C 산식으로 산출했습니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
            "key_factors": [
                "DART 재무 Rule",
                "DART 이벤트 공시",
                "뉴스 Trigger 분리",
                "Risk Gate 별도 적용",
            ],
            "news": sorted(news_cards, key=lambda x: x.get("severity", 0), reverse=True)[:5],
            "source_status": {
                "dart_financial": source_status_global["dart_financial"],
                "dart_disclosure": source_status_global["dart_disclosure"],
                "news": source_status_global["news"],
                "kind_rss": source_status_global["kind_rss"],
                "rating": "manual_file_needed",
            },
            "missing_fields": sorted(set(missing_fields)),
            "rule_breakdown": rule_breakdown[:8],
        })

    issuers = sorted(issuers, key=lambda x: x["final_score"], reverse=True)
    for rank, issuer in enumerate(issuers, 1):
        issuer["rank"] = rank

    next_run = (t + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return {
        "as_of_date": t.strftime("%Y-%m-%d"),
        "policy_version": "v1.3-static-mvp-option-c-api-expanded",
        "service": {
            "name": "발행사 선제 영업 플랫폼",
            "subtitle": "공개정보 기반 자금수요 레이더",
            "description": "DART 재무/이벤트 공시, 뉴스, 선택형 KIND RSS를 기반으로 향후 자금조달 필요성이 높은 발행사를 선제 탐지합니다.",
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
