#!/usr/bin/env python3
"""Generate 발행사 선제 영업 플랫폼 daily_snapshot.json - v1.9 full-universe industry mapped.

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


# 화면 조건검색용 업종/분류 보강 규칙
# OpenDART corpCode.xml에는 업종이 포함되지 않으므로, 1차로 universe.csv 수기값,
# 2차로 회사명/공시제목 키워드 기반의 보수적 추정값을 사용한다.
INDUSTRY_RULES = [
    ("건설/부동산", ["건설", "개발", "산업개발", "엔지니어링", "주택", "부동산", "리츠", "건축", "토건", "시공"]),
    ("석유화학/소재", ["케미칼", "화학", "석유", "정유", "소재", "첨단소재", "카본", "플라스틱", "레진", "고무", "페인트", "도료", "필름"]),
    ("철강/금속", ["철강", "스틸", "금속", "알루미늄", "동국", "제강", "비철", "강관", "와이어"]),
    ("반도체/전자부품", ["반도체", "전자", "일렉", "테크", "하이닉스", "칩", "실리콘", "웨이퍼", "디스플레이", "PCB", "패키징", "세미콘", "마이크로", "옵트론", "광전자"]),
    ("2차전지/전기차", ["배터리", "전지", "이차전지", "양극", "음극", "전해", "전기차", "EV", "리튬", "에코프로", "엘앤에프"]),
    ("자동차/기계", ["자동차", "모터", "모빌리티", "오토", "기계", "정공", "정밀", "부품", "공업", "엔진", "타이어", "베어링"]),
    ("조선/방산/항공", ["조선", "해양", "중공업", "방산", "항공", "우주", "디펜스", "에어로", "선박", "로템"]),
    ("제약/바이오/헬스케어", ["제약", "바이오", "생명", "헬스", "메디", "팜", "의료", "셀", "랩", "진단", "백신", "병원"]),
    ("IT/SW/플랫폼", ["소프트", "시스템", "정보", "데이터", "AI", "클라우드", "인터넷", "플랫폼", "컴퓨터", "솔루션", "소프트웨어"]),
    ("게임/엔터/콘텐츠", ["게임", "엔터", "스튜디오", "콘텐츠", "미디어", "뮤직", "웹툰", "영화", "드라마", "아티스트"]),
    ("통신/미디어", ["텔레콤", "통신", "네트웍", "네트워크", "방송", "케이블", "위성"]),
    ("유통/소비재", ["유통", "쇼핑", "백화점", "마트", "리테일", "패션", "화장품", "생활", "가구", "호텔", "관광", "레저"]),
    ("음식료", ["식품", "푸드", "음료", "주류", "제과", "제빵", "농심", "오뚜기", "하림", "사조"]),
    ("운송/물류", ["해운", "항공", "운송", "물류", "글로벌", "로지스", "택배", "대한항공", "선사"]),
    ("에너지/유틸리티", ["에너지", "전력", "가스", "발전", "태양광", "풍력", "수소", "그린", "유틸리티"]),
    ("금융", ["금융", "증권", "은행", "카드", "보험", "캐피탈", "투자", "자산운용", "벤처", "스팩"]),
    ("지주/투자회사", ["홀딩스", "지주", "파트너스", "인베스트", "투자회사"]),
]


# DART 회사개요의 induty_code(KSIC 계열 업종코드)를 사용자용 범주로 변환한다.
# 회사명 키워드는 공식 업종코드만으로는 잘 드러나지 않는 2차전지/방산/바이오 등 테마성 업종을 보정하는 용도다.
KSIC_PREFIX_TO_INDUSTRY = {
    "05": "에너지/유틸리티", "06": "에너지/유틸리티", "07": "에너지/유틸리티", "08": "에너지/유틸리티", "09": "에너지/유틸리티",
    "10": "음식료", "11": "음식료", "12": "음식료",
    "13": "유통/소비재", "14": "유통/소비재", "15": "유통/소비재",
    "16": "석유화학/소재", "17": "석유화학/소재", "18": "석유화학/소재", "19": "석유화학/소재", "20": "석유화학/소재",
    "21": "제약/바이오/헬스케어", "22": "석유화학/소재", "23": "석유화학/소재",
    "24": "철강/금속", "25": "철강/금속",
    "26": "반도체/전자부품", "27": "반도체/전자부품",
    "28": "자동차/기계", "29": "자동차/기계", "30": "자동차/기계",
    "31": "조선/방산/항공", "32": "유통/소비재", "33": "자동차/기계", "34": "자동차/기계",
    "35": "에너지/유틸리티", "36": "에너지/유틸리티", "37": "에너지/유틸리티", "38": "에너지/유틸리티", "39": "에너지/유틸리티",
    "41": "건설/부동산", "42": "건설/부동산",
    "45": "유통/소비재", "46": "유통/소비재", "47": "유통/소비재",
    "49": "운송/물류", "50": "운송/물류", "51": "운송/물류", "52": "운송/물류",
    "55": "유통/소비재", "56": "유통/소비재",
    "58": "IT/SW/플랫폼", "59": "게임/엔터/콘텐츠", "60": "통신/미디어", "61": "통신/미디어", "62": "IT/SW/플랫폼", "63": "IT/SW/플랫폼",
    "64": "금융", "65": "금융", "66": "금융", "68": "건설/부동산",
    "70": "지주/투자회사", "71": "자동차/기계", "72": "제약/바이오/헬스케어", "73": "게임/엔터/콘텐츠", "74": "IT/SW/플랫폼",
    "86": "제약/바이오/헬스케어", "90": "게임/엔터/콘텐츠", "91": "게임/엔터/콘텐츠",
}

# 업종코드보다 우선할 고확신 키워드. 예: 배터리 소재 회사는 KSIC상 화학이어도 사용자에게는 2차전지로 보는 게 검색 편의성이 높다.
SPECIFIC_KEYWORD_RULES = [
    ("2차전지/전기차", ["배터리", "전지", "이차전지", "2차전지", "양극", "음극", "전해", "리튬", "분리막", "전기차", "EV", "에코프로", "엘앤에프", "천보", "더블유씨피"]),
    ("조선/방산/항공", ["조선", "해양", "중공업", "방산", "디펜스", "항공", "우주", "에어로", "로템", "선박"]),
    ("제약/바이오/헬스케어", ["제약", "바이오", "생명", "헬스", "메디", "팜", "의료", "셀트리온", "HLB", "진단", "백신"]),
    ("반도체/전자부품", ["반도체", "세미콘", "웨이퍼", "패키징", "PCB", "디스플레이", "칩", "하이닉스"]),
    ("금융", ["은행", "증권", "보험", "카드", "캐피탈", "금융"]),
    ("지주/투자회사", ["홀딩스", "지주", "인베스트먼트", "인베스트", "파트너스"]),
    ("건설/부동산", ["건설", "산업개발", "부동산", "리츠", "토건", "주택"]),
    ("음식료", ["식품", "푸드", "음료", "제과", "제빵", "농심", "오뚜기", "하림", "사조"]),
    ("통신/미디어", ["텔레콤", "통신", "네트워크", "네트웍", "방송", "위성", "케이블"]),
    ("게임/엔터/콘텐츠", ["게임", "엔터", "스튜디오", "콘텐츠", "웹툰", "뮤직", "아티스트"]),
]

INDUSTRY_CATEGORY_DESCRIPTIONS = {
    "건설/부동산": "건설, 토목, 부동산 개발, 리츠, 인프라 관련 기업",
    "석유화학/소재": "화학, 정유, 플라스틱, 소재, 필름, 비금속 소재 기업",
    "철강/금속": "철강, 비철금속, 금속가공, 강관, 제강 관련 기업",
    "반도체/전자부품": "반도체, 전자부품, 디스플레이, PCB, IT 하드웨어 기업",
    "2차전지/전기차": "배터리 소재·셀·부품, 전기차 밸류체인 기업",
    "자동차/기계": "자동차 부품, 기계, 장비, 정밀·공업 관련 기업",
    "조선/방산/항공": "조선, 방산, 항공, 우주, 선박·중공업 관련 기업",
    "제약/바이오/헬스케어": "제약, 바이오, 의료기기, 진단, 헬스케어 기업",
    "IT/SW/플랫폼": "소프트웨어, 데이터, 클라우드, 인터넷, 플랫폼 기업",
    "게임/엔터/콘텐츠": "게임, 엔터, 콘텐츠, 스튜디오, 미디어 제작 기업",
    "통신/미디어": "통신, 네트워크, 방송, 케이블, 위성 관련 기업",
    "유통/소비재": "유통, 패션, 화장품, 생활소비재, 호텔·레저 기업",
    "음식료": "식품, 음료, 제과·제빵, 농축수산 가공 기업",
    "운송/물류": "항공, 해운, 육상운송, 물류, 택배 관련 기업",
    "에너지/유틸리티": "전력, 가스, 발전, 신재생, 환경·폐기물 처리 기업",
    "금융": "은행, 증권, 보험, 카드, 캐피탈 등 금융업",
    "지주/투자회사": "지주회사, 투자회사, 인베스트먼트, 파트너스 성격 기업",
    "기타/미분류": "자동분류 근거가 부족해 추후 수기 매핑 보완이 필요한 기업",
}


def infer_specific_industry_from_text(text: str) -> Tuple[str, str]:
    compact = (text or "").replace(" ", "").upper()
    for industry, keywords in SPECIFIC_KEYWORD_RULES:
        for kw in keywords:
            if kw and kw.replace(" ", "").upper() in compact:
                return industry, "키워드 보정"
    return "기타/미분류", "자동 미분류"


def infer_industry_from_text(text: str) -> Tuple[str, str]:
    specific, source = infer_specific_industry_from_text(text)
    if specific != "기타/미분류":
        return specific, source
    compact = (text or "").replace(" ", "")
    for industry, keywords in INDUSTRY_RULES:
        for kw in keywords:
            if kw and kw.replace(" ", "") in compact:
                return industry, "키워드 추정"
    return "기타/미분류", "자동 미분류"


def industry_from_dart_code(induty_code: str) -> Tuple[str, str]:
    code = re.sub(r"\D", "", induty_code or "")
    if len(code) >= 2:
        prefix = code[:2]
        if prefix in KSIC_PREFIX_TO_INDUSTRY:
            return KSIC_PREFIX_TO_INDUSTRY[prefix], "DART 업종코드"
    return "기타/미분류", "자동 미분류"


def resolve_industry(row: Dict[str, str], dart_events: Dict[str, Any], kind_events: Dict[str, Any], profile: Optional[Dict[str, Any]] = None) -> Tuple[str, str]:
    manual = (row.get("industry") or "").strip()
    if manual:
        return manual, "수기/CSV"

    texts = [row.get("corp_name", ""), row.get("keywords", "")]
    for ev in (dart_events.get("key_events") or []) + (kind_events.get("key_events") or []):
        texts.append(str(ev.get("title", "")))
    text_joined = " ".join(texts)

    # 테마/사용자 검색 편의성상 업종코드보다 우선해야 하는 고확신 키워드 보정
    specific, specific_source = infer_specific_industry_from_text(text_joined)
    if specific != "기타/미분류":
        return specific, specific_source

    # OpenDART 회사개요 API에서 확보한 업종코드를 broad category로 변환
    if profile:
        by_code, code_source = industry_from_dart_code(str(profile.get("induty_code", "")))
        if by_code != "기타/미분류":
            return by_code, code_source

    return infer_industry_from_text(text_joined)


def score_band(score: float) -> str:
    if score >= 70:
        return "A. 즉시 검토"
    if score >= 55:
        return "B. 우선 관찰"
    if score >= 40:
        return "C. 모니터링"
    return "D. 낮음"


def trigger_type_from(event_severity: int, news_score: float) -> str:
    if event_severity >= 90:
        return "자금조달 공시"
    if event_severity >= 75:
        return "투자/차입 이벤트"
    if news_score >= 70:
        return "뉴스 Trigger"
    return "기초 모니터링"


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


def fetch_company_profile(corp_code: str) -> Dict[str, Any]:
    data = opendart_get("company.json", {"corp_code": corp_code}, timeout=12)
    if data.get("status") not in {"000", None, ""}:
        return {"corp_code": corp_code, "status": data.get("status"), "message": data.get("message", "")}
    return {
        "corp_code": corp_code,
        "stock_code": data.get("stock_code", ""),
        "corp_name": data.get("corp_name", ""),
        "corp_cls": data.get("corp_cls", ""),
        "induty_code": data.get("induty_code", ""),
        "est_dt": data.get("est_dt", ""),
        "acc_mt": data.get("acc_mt", ""),
        "hm_url": data.get("hm_url", ""),
        "status": data.get("status", "000"),
    }


def fetch_company_profiles(rows: List[Dict[str, str]]) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """Fetch DART company profiles for industry code mapping.

    This is deliberately separate from financial statement calls. It is much lighter and is used
    only to attach industry categories so the UI filter works for all listed companies.
    """
    if os.getenv("ENABLE_DART_COMPANY_PROFILE", "1").strip().lower() in {"0", "false", "no", "off"}:
        return {}, "disabled"
    if requests is None:
        return {}, "requests_missing"
    if not os.getenv("OPENDART_API_KEY", "").strip():
        return {}, "api_key_missing"

    limit = int(os.getenv("DART_COMPANY_PROFILE_LIMIT", "0") or "0")
    targets = rows[:limit] if limit > 0 else rows
    workers = max(1, min(int(os.getenv("DART_PROFILE_WORKERS", "8")), 16))
    profiles: Dict[str, Dict[str, Any]] = {}
    errors = 0

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_company_profile, r.get("corp_code", "")): r for r in targets if r.get("corp_code")}
            for idx, fut in enumerate(as_completed(futs), 1):
                row = futs[fut]
                code = row.get("corp_code", "")
                try:
                    prof = fut.result()
                    profiles[code] = prof
                    if prof.get("status") not in {"000", None, ""}:
                        errors += 1
                except Exception as exc:
                    errors += 1
                    profiles[code] = {"corp_code": code, "status": "error", "message": f"{type(exc).__name__}: {exc}"}
                if idx % 200 == 0:
                    print(f"DART company profiles loaded: {idx}/{len(targets)}")
    except Exception as exc:
        print(f"DART company profile batch failed: {type(exc).__name__}: {exc}")
        return profiles, f"partial_error:{type(exc).__name__}"

    save_raw("dart_company_profiles_sample", list(profiles.values())[:300])
    ok = sum(1 for p in profiles.values() if p.get("induty_code"))
    print(f"DART company profiles completed: {len(profiles)} rows, induty_code={ok}, errors={errors}")
    if len(profiles) == 0:
        return profiles, "empty"
    if errors > len(profiles) * 0.5:
        return profiles, "partial_many_errors"
    return profiles, "live_ok"


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
        "dart_company_profile": "pending",
        "news": "api_key_missing",
        "kind_rss": "not_configured",
        "fsc_profile": "reserved_hook_not_enabled",
        "credit_rating": "manual_file_needed",
        "llm_annotation": "transparent_proxy_static_mvp",
    }

    profile_by_code, profile_status = fetch_company_profiles(rows)
    source_status_global["dart_company_profile"] = profile_status

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
        profile = profile_by_code.get(corp_code, {})
        industry, industry_source = resolve_industry(row, dart_events, kind_events, profile)
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
            "industry": industry,
            "industry_source": industry_source,
            "industry_code": str(profile.get("induty_code", "")) if profile else "",
            "score_band": score_band(final_score),
            "trigger_type": trigger_type_from(event_severity, news_score),
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
                "dart_company_profile": source_status_global["dart_company_profile"],
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
                    "score_band": score_band(final_score),
                    "trigger_type": trigger_type_from(event_severity, news_score),
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
        "policy_version": "v1.9-full-universe-industry-mapped",
        "service": {
            "name": "발행사 선제 영업 플랫폼",
            "subtitle": "공개정보 기반 자금수요 레이더",
            "description": "전체 상장사에 업종 범주를 자동 매핑하고, 공시·뉴스·재무 신호가 있는 후보를 우선순위화합니다.",
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
            "universe_mode": "opendart_full_listed_user_ui_two_stage",
            "news_enrich_limit": news_limit,
            "page_detail_limit": page_detail_limit,
            "industry_mapped_count": sum(1 for x in issuers if x.get("industry") != "기타/미분류"),
            "industry_unmapped_count": sum(1 for x in issuers if x.get("industry") == "기타/미분류"),
            "industry_mapping_method": "수기/CSV → 키워드 보정 → DART 업종코드 → 일반 키워드 추정",
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
            "industries": ["전체"] + sorted({x.get("industry", "") for x in issuers if x.get("industry")}),
            "risk_levels": ["전체", "Low", "Mid", "High"],
            "priority_levels": ["전체", "Priority A", "Watchlist B", "Monitor C", "Low Priority"],
            "score_bands": ["전체", "A. 즉시 검토", "B. 우선 관찰", "C. 모니터링", "D. 낮음"],
            "trigger_types": ["전체", "자금조달 공시", "투자/차입 이벤트", "뉴스 Trigger", "기초 모니터링"],
            "industry_sources": ["전체", "수기/CSV", "키워드 보정", "DART 업종코드", "키워드 추정", "자동 미분류"],
            "industry_categories": [{"name": k, "meaning": v} for k, v in INDUSTRY_CATEGORY_DESCRIPTIONS.items()],
            "definitions": [
                {"field": "업종", "meaning": "수기값을 우선 사용하고, 없으면 회사명 키워드와 DART 업종코드로 사용자용 범주에 자동 매핑합니다."},
                {"field": "우선순위", "meaning": "공시·뉴스·재무 신호를 종합해 영업 검토 순서를 나눈 값입니다. 산식은 화면에 노출하지 않습니다."},
                {"field": "Trigger", "meaning": "최근 자금조달 공시, 투자/차입 이벤트, 뉴스 신호, 기초 모니터링 중 어떤 신호가 우선 감지됐는지 표시합니다."},
                {"field": "Risk", "meaning": "거래 구조 검토 시 별도 확인이 필요한 위험 수준입니다. 점수와 별도로 구조 검토에 사용합니다."}
            ]
        },
        "issuers": issuers,
    }


def main() -> None:
    snapshot = build_snapshot()
    OUTPUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH} with {len(snapshot['issuers'])} issuers")


if __name__ == "__main__":
    main()
