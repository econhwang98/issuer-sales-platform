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
import hashlib
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
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


MANUAL_INDUSTRY_ALIASES = {
    "건설": "건설/부동산",
    "부동산": "건설/부동산",
    "소재": "석유화학/소재",
    "화학": "석유화학/소재",
    "석유화학": "석유화학/소재",
    "철강": "철강/금속",
    "금속": "철강/금속",
    "전자부품": "반도체/전자부품",
    "반도체": "반도체/전자부품",
    "전기차": "2차전지/전기차",
    "2차전지": "2차전지/전기차",
    "자동차": "자동차/기계",
    "기계": "자동차/기계",
    "조선": "조선/방산/항공",
    "방산": "조선/방산/항공",
    "항공": "조선/방산/항공",
    "바이오": "제약/바이오/헬스케어",
    "제약": "제약/바이오/헬스케어",
    "헬스케어": "제약/바이오/헬스케어",
    "SW": "IT/SW/플랫폼",
    "소프트웨어": "IT/SW/플랫폼",
    "플랫폼": "IT/SW/플랫폼",
    "엔터": "게임/엔터/콘텐츠",
    "콘텐츠": "게임/엔터/콘텐츠",
    "미디어": "통신/미디어",
    "통신": "통신/미디어",
    "유통": "유통/소비재",
    "소비재": "유통/소비재",
    "식품": "음식료",
    "운송": "운송/물류",
    "물류": "운송/물류",
    "에너지": "에너지/유틸리티",
    "유틸리티": "에너지/유틸리티",
    "지주": "지주/투자회사",
    "투자회사": "지주/투자회사",
    "기타": "기타/미분류",
    "미분류": "기타/미분류",
}


def canonicalize_industry(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return "기타/미분류"
    if text in INDUSTRY_CATEGORY_DESCRIPTIONS:
        return text
    compact = re.sub(r"\s+", "", text)
    if compact in MANUAL_INDUSTRY_ALIASES:
        return MANUAL_INDUSTRY_ALIASES[compact]
    for canonical in INDUSTRY_CATEGORY_DESCRIPTIONS:
        if compact and compact in re.sub(r"\s+", "", canonical):
            return canonical
    return text


def ordered_industry_filters(issuers: List[Dict[str, Any]]) -> List[str]:
    seen = {x.get("industry", "") for x in issuers if x.get("industry")}
    known = [x for x in INDUSTRY_CATEGORY_DESCRIPTIONS if x in seen]
    extra = sorted(x for x in seen if x not in INDUSTRY_CATEGORY_DESCRIPTIONS)
    return ["전체"] + known + extra


RATING_CSV_PATH = ROOT / "credit_ratings.csv"
RATING_AGENCY_ALIASES = {
    "kis": "한국신용평가",
    "한국신용평가": "한국신용평가",
    "한국신용평가㈜": "한국신용평가",
    "korea investors service": "한국신용평가",
    "koreainvestorsservice": "한국신용평가",
    "nice": "NICE신용평가",
    "nice신용평가": "NICE신용평가",
    "nice신용평가㈜": "NICE신용평가",
    "나이스신용평가": "NICE신용평가",
    "kr": "한국기업평가",
    "korea ratings": "한국기업평가",
    "korearatings": "한국기업평가",
    "한국기업평가": "한국기업평가",
    "한국기업평가㈜": "한국기업평가",
}
LONG_RATING_TYPES = ["장기", "회사채", "무보증사채", "일반사채", "특수채", "issuer", "icr", "sb", "pfb", "기업신용등급"]
SHORT_RATING_TYPES = ["단기", "기업어음", "cp", "단기사채", "stb", "abcp", "abstb"]
LONG_RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "BB-", "B+", "B", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"]
SHORT_RATING_ORDER = ["A1", "A2+", "A2", "A2-", "A3+", "A3", "A3-", "B+", "B", "B-", "C", "D"]


def normalize_company_key(value: str) -> str:
    text = (value or "").lower()
    text = re.sub(r"\(주\)|㈜|주식회사|\(유\)|유한회사|co\.?,?\s*ltd\.?", "", text)
    text = re.sub(r"[^0-9a-z가-힣]", "", text)
    return text


def canonical_rating_agency(value: str) -> str:
    key = re.sub(r"\s+", "", (value or "").strip()).lower()
    if key in RATING_AGENCY_ALIASES:
        return RATING_AGENCY_ALIASES[key]
    return (value or "외부등급").strip() or "외부등급"


def row_value(row: Dict[str, str], *names: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        if name in row and row.get(name):
            return str(row.get(name, "")).strip()
        value = lowered.get(name.lower())
        if value:
            return str(value).strip()
    return ""


def split_rating_outlook(value: str, fallback_outlook: str = "") -> Tuple[str, str]:
    text = (value or "").strip()
    if not text:
        return "", fallback_outlook
    outlook = fallback_outlook.strip()
    match = re.search(r"\(([^)]+)\)", text)
    if match and not outlook:
        outlook = match.group(1).strip()
    rating = re.sub(r"\([^)]*\)", "", text).strip()
    rating = re.sub(r"\s+", "", rating)
    return rating, outlook


def infer_rating_bucket(rating_type: str, rating: str) -> str:
    text = f"{rating_type} {rating}".lower()
    if any(token in text for token in SHORT_RATING_TYPES):
        return "short"
    if any(token in text for token in LONG_RATING_TYPES):
        return "long"
    if re.match(r"^a[1-3][+-]?(\(sf\))?$", (rating or "").lower()):
        return "short"
    if rating:
        return "long"
    return ""


def parse_credit_rating_row(row: Dict[str, str]) -> List[Dict[str, str]]:
    corp_name = row_value(row, "corp_name", "company", "회사명", "기업명", "issuer")
    ticker = row_value(row, "ticker", "종목코드", "stock_code")
    corp_code = row_value(row, "corp_code", "고유번호", "dart_corp_code")
    agency = canonical_rating_agency(row_value(row, "agency", "신평사", "평가사", "rating_agency"))
    rating_date = row_value(row, "rating_date", "평가일", "date", "등급일")
    source_url = row_value(row, "source_url", "url", "출처")
    outlook = row_value(row, "outlook", "등급전망", "watch", "rating_outlook")
    records: List[Dict[str, str]] = []

    long_rating, long_outlook = split_rating_outlook(row_value(row, "long_term_rating", "장기신용등급", "장기등급"), outlook)
    short_rating, short_outlook = split_rating_outlook(row_value(row, "short_term_rating", "단기신용등급", "단기등급"), outlook)
    if long_rating:
        records.append({"corp_name": corp_name, "ticker": ticker, "corp_code": corp_code, "agency": agency, "bucket": "long", "rating": long_rating, "outlook": long_outlook, "rating_date": rating_date, "source_url": source_url})
    if short_rating:
        records.append({"corp_name": corp_name, "ticker": ticker, "corp_code": corp_code, "agency": agency, "bucket": "short", "rating": short_rating, "outlook": short_outlook, "rating_date": rating_date, "source_url": source_url})

    generic_rating, generic_outlook = split_rating_outlook(row_value(row, "rating", "current_rating", "현재등급", "등급"), outlook)
    if generic_rating:
        rating_type = row_value(row, "rating_type", "instrument_type", "종류", "평가종류", "구분")
        bucket = infer_rating_bucket(rating_type, generic_rating)
        if bucket:
            records.append({"corp_name": corp_name, "ticker": ticker, "corp_code": corp_code, "agency": agency, "bucket": bucket, "rating": generic_rating, "outlook": generic_outlook, "rating_date": rating_date, "source_url": source_url})
    return [record for record in records if record.get("corp_name") or record.get("ticker") or record.get("corp_code")]


def read_credit_rating_records() -> Tuple[List[Dict[str, str]], str]:
    path = Path(os.getenv("CREDIT_RATINGS_CSV", str(RATING_CSV_PATH))).expanduser()
    if not path.exists():
        return [], "csv_missing"
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        records: List[Dict[str, str]] = []
        for row in rows:
            records.extend(parse_credit_rating_row(row))
        return records, f"csv_loaded:{len(records)}"
    except Exception as exc:
        return [], f"csv_error:{type(exc).__name__}"


def build_credit_rating_index(records: List[Dict[str, str]]) -> Dict[str, List[Dict[str, str]]]:
    index: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for record in records:
        keys = [
            f"corp_code:{record.get('corp_code', '').strip()}",
            f"ticker:{record.get('ticker', '').strip()}",
            f"name:{normalize_company_key(record.get('corp_name', ''))}",
        ]
        for key in keys:
            if key.split(":", 1)[1]:
                index[key].append(record)
    return index


def latest_record(records: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not records:
        return None
    return sorted(records, key=lambda x: x.get("rating_date", ""), reverse=True)[0]


def rating_rank(rating: str, order: List[str]) -> int:
    clean = re.sub(r"\(sf\)", "", (rating or "").replace("*", "").replace("※", "").replace(" ", ""))
    if clean in order:
        return order.index(clean)
    return len(order) + 100


def outlook_rank(outlook: str) -> int:
    text = (outlook or "").strip()
    if text in {"긍정적", "Positive"}:
        return 0
    if text in {"안정적", "Stable", ""}:
        return 1
    if text in {"유동적", "Developing"}:
        return 2
    if text in {"부정적", "Negative"}:
        return 3
    return 1


def worst_agency_rating(agency_rows: List[Dict[str, str]], rating_key: str, outlook_key: str, date_key: str, order: List[str]) -> Optional[Dict[str, str]]:
    candidates = [x for x in agency_rows if x.get(rating_key) and x.get(rating_key) != "무등급"]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda x: (rating_rank(x.get(rating_key, ""), order), outlook_rank(x.get(outlook_key, "")), x.get(date_key, "")),
        reverse=True,
    )[0]


def credit_rating_for_issuer(row: Dict[str, str], index: Dict[str, List[Dict[str, str]]]) -> Dict[str, Any]:
    keys = [
        f"corp_code:{(row.get('corp_code') or '').strip()}",
        f"ticker:{(row.get('ticker') or '').strip()}",
        f"name:{normalize_company_key(row.get('corp_name', ''))}",
    ]
    records: List[Dict[str, str]] = []
    seen = set()
    for key in keys:
        for record in index.get(key, []):
            record_key = tuple(sorted(record.items()))
            if record_key not in seen:
                seen.add(record_key)
                records.append(record)

    agency_rows = []
    for agency in ["한국신용평가", "NICE신용평가", "한국기업평가"]:
        agency_records = [x for x in records if x.get("agency") == agency]
        long_record = latest_record([x for x in agency_records if x.get("bucket") == "long"])
        short_record = latest_record([x for x in agency_records if x.get("bucket") == "short"])
        agency_rows.append({
            "agency": agency,
            "long_term_rating": long_record.get("rating", "무등급") if long_record else "무등급",
            "long_term_outlook": long_record.get("outlook", "") if long_record else "",
            "long_term_date": long_record.get("rating_date", "") if long_record else "",
            "short_term_rating": short_record.get("rating", "무등급") if short_record else "무등급",
            "short_term_outlook": short_record.get("outlook", "") if short_record else "",
            "short_term_date": short_record.get("rating_date", "") if short_record else "",
            "source_url": (long_record or short_record or {}).get("source_url", "") if (long_record or short_record) else "",
        })

    long_summary = worst_agency_rating(agency_rows, "long_term_rating", "long_term_outlook", "long_term_date", LONG_RATING_ORDER)
    short_summary = worst_agency_rating(agency_rows, "short_term_rating", "short_term_outlook", "short_term_date", SHORT_RATING_ORDER)
    all_dates = [x.get("long_term_date", "") for x in agency_rows] + [x.get("short_term_date", "") for x in agency_rows]
    status = "유효등급" if long_summary or short_summary else "무등급"
    return {
        "credit_rating_status": status,
        "long_term_rating": long_summary.get("long_term_rating", "무등급") if long_summary else "무등급",
        "long_term_outlook": long_summary.get("long_term_outlook", "") if long_summary else "",
        "long_term_rating_agency": long_summary.get("agency", "") if long_summary else "",
        "short_term_rating": short_summary.get("short_term_rating", "무등급") if short_summary else "무등급",
        "short_term_outlook": short_summary.get("short_term_outlook", "") if short_summary else "",
        "short_term_rating_agency": short_summary.get("agency", "") if short_summary else "",
        "credit_rating_agencies": agency_rows,
        "credit_rating_last_date": max([x for x in all_dates if x], default=""),
        "credit_rating_source": "credit_ratings.csv" if status == "유효등급" else "미매칭",
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
        return canonicalize_industry(manual), "수기/CSV"

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
    if score >= 82:
        return "S. 긴급 검토"
    if score >= 70:
        return "A. 즉시 접촉"
    if score >= 58:
        return "B. 구조화 검토"
    if score >= 46:
        return "C. 관심 관찰"
    return "D. 정기 모니터링"


def trigger_type_from(event_severity: int, news_score: float, event_titles: Optional[List[str]] = None) -> str:
    text = " ".join(event_titles or [])
    if any(k in text for k in ["채무불이행", "자본잠식", "회생", "감사의견", "상장폐지", "관리종목"]):
        return "신용/계속기업 리스크"
    if any(k in text for k in ["유상증자", "전환사채", "신주인수권", "교환사채", "회사채", "CP", "사채"]):
        return "자금조달 직접공시"
    if any(k in text for k in ["단기차입금", "차입", "채무보증", "담보제공", "리파이낸싱"]):
        return "차입/담보 이벤트"
    if any(k in text for k in ["유형자산", "시설투자", "공장", "CAPEX", "증설"]):
        return "투자·CAPEX 이벤트"
    if any(k in text for k in ["타법인", "출자증권", "인수", "합병", "M&A"]):
        return "M&A/지분투자 이벤트"
    if event_severity >= 90:
        return "자금조달 직접공시"
    if event_severity >= 75:
        return "투자/차입 이벤트"
    if news_score >= 70:
        return "업황/뉴스 압력"
    return "재무구조 점검"


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


SECTOR_BASE_RISK = {
    "건설/부동산": 58, "석유화학/소재": 50, "철강/금속": 46, "반도체/전자부품": 42,
    "2차전지/전기차": 55, "자동차/기계": 43, "조선/방산/항공": 40, "제약/바이오/헬스케어": 54,
    "IT/SW/플랫폼": 38, "게임/엔터/콘텐츠": 46, "통신/미디어": 34, "유통/소비재": 43,
    "음식료": 28, "운송/물류": 47, "에너지/유틸리티": 36, "금융": 30, "지주/투자회사": 42,
    "기타/미분류": 40,
}

SECTOR_DEFAULT_NEED = {
    "건설/부동산": "PF·운영자금/차환", "석유화학/소재": "업황 방어/차환", "철강/금속": "운전자금/재고금융",
    "반도체/전자부품": "CAPEX/운전자금", "2차전지/전기차": "성장 CAPEX/메자닌", "자동차/기계": "운전자금/설비투자",
    "조선/방산/항공": "수주 기반 운전자금", "제약/바이오/헬스케어": "R&D/자본확충", "IT/SW/플랫폼": "성장자금/운영자금",
    "게임/엔터/콘텐츠": "콘텐츠 투자/운영자금", "통신/미디어": "설비투자/차환", "유통/소비재": "운전자금/리테일 투자",
    "음식료": "운전자금/원재료 부담", "운송/물류": "운전자금/리스·선박·항공기 금융", "에너지/유틸리티": "프로젝트/설비투자",
    "금융": "자본비율/시장성 조달", "지주/투자회사": "포트폴리오 투자/차환", "기타/미분류": "기초 모니터링",
}

def stable_int(seed: str, modulo: int = 100) -> int:
    h = hashlib.sha256((seed or "-").encode("utf-8")).hexdigest()
    return int(h[:8], 16) % modulo

def _join_event_text(news_cards: List[Dict[str, Any]]) -> str:
    return " ".join(str(x.get("title", "")) for x in news_cards or [])

def _risk_label(score: float, hard_event: bool = False) -> str:
    if hard_event and score >= 88:
        return "Critical"
    if score >= 82:
        return "High"
    if score >= 70:
        return "Elevated"
    if score >= 58:
        return "Moderate"
    if score >= 45:
        return "Watch"
    return "Low"

def _structure_group_and_terms(funding_need: str, risk_level: str, industry: str, event_text: str) -> Tuple[str, str, str]:
    high_risk = risk_level in {"Critical", "High", "Elevated"}
    if any(k in event_text for k in ["채무불이행", "회생", "감사의견", "상장폐지", "자본잠식"]):
        return "구조조정/자본확충", "유상증자·CB·RCPS·채무재조정 병행 검토", "원문 확인 후 Hold/조건부 접근 · 담보/자본확충 선행"
    if any(k in event_text for k in ["유상증자"]):
        return "ECM/자본확충", "유상증자 주선·주주배정/제3자배정 자문", "증자 목적·최대주주 참여·할인율·실권 리스크 검토"
    if any(k in event_text for k in ["전환사채", "신주인수권", "교환사채", "CB", "BW", "EB"]):
        return "메자닌", "CB/BW/EB·RCPS 구조화 검토", "전환가액 리픽싱·콜옵션·Put·희석률·보호예수 확인"
    if any(k in event_text for k in ["회사채", "CP", "사채"]):
        return "시장성 차입/차환", "회사채·사모사채·CP·신용보강부 구조", "만기 6~24M · 등급/수요예측/담보·보증 가능성 확인"
    if any(k in event_text for k in ["단기차입금", "차입", "채무보증", "담보제공", "리파이낸싱"]):
        return "담보부/브릿지 차입", "담보부 대출·ABL·브릿지론·운전자금 라인", "6~18M · 담보가치·현금흐름·차환 가능성 우선 점검"
    if any(k in event_text for k in ["유형자산", "시설투자", "CAPEX", "증설", "공장"]):
        return "CAPEX 금융", "설비금융·프로젝트론·세일앤리스백·메자닌", "투자기간/가동률/수주계약·담보권 설정 가능성 검토"
    if any(k in event_text for k in ["타법인", "출자증권", "인수", "합병", "M&A"]):
        return "인수/투자금융", "인수금융·브릿지론·메자닌·공동투자", "취득목적·PMI·재원조달·재무부담 증가 여부 확인"
    if industry == "건설/부동산":
        return "PF/부동산 금융", "PF 리파이낸싱·담보부 대출·브릿지론", "사업장별 분양률·공정률·우발채무·담보순위 확인"
    if industry in {"제약/바이오/헬스케어", "2차전지/전기차"}:
        return "성장자금/메자닌", "CB·RCPS·전략투자·성장자금 라운드", "마일스톤/수주/기술가치·희석률 및 후속조달 가능성 확인"
    if high_risk:
        return "신용보강 필요 차입", "담보부 대출·ABL·사모사채·신용보강부 구조", "담보·보증·코버넌트 포함, 원문 공시 확인 후 진행"
    return "정기 모니터링", "시장성 조달·운영자금 라인 모니터링", "신규 자금조달 공시, 실적 발표, 신용 이벤트 발생 시 접촉 타이밍 재판단"

def enhanced_finance_classification(issuer_seed: str, industry: str, rule_score: float, news_score: float, event_score: int, news_cards: List[Dict[str, Any]], missing_fields: List[str]) -> Dict[str, Any]:
    event_text = _join_event_text(news_cards)
    hard_event = any(k in event_text for k in ["채무불이행", "자본잠식", "회생", "감사의견", "상장폐지", "유상증자", "전환사채", "신주인수권"])
    sector = SECTOR_BASE_RISK.get(industry, 40)
    variation = stable_int(issuer_seed, 17) - 8
    event_component = min(40, event_score * 0.42) if event_score else 0
    news_component = max(0, (news_score - 50) * 0.35) if news_score else 0
    risk_numeric = clip(sector + variation + event_component + news_component, 0, 100)
    risk_level = _risk_label(risk_numeric, hard_event=hard_event)

    if any(k in event_text for k in ["채무불이행", "회생", "감사의견", "상장폐지"]):
        funding_need = "구조조정/유동성 방어"
        risk_type = "계속기업/채무불이행 리스크"
        action_stage = "Hold / 원문 확인"
    elif "자본잠식" in event_text or "유상증자" in event_text:
        funding_need = "자본확충/재무구조 개선"
        risk_type = "자본확충·희석 리스크"
        action_stage = "즉시 접촉" if risk_level in {"Elevated", "High", "Critical"} else "구조 검토"
    elif any(k in event_text for k in ["전환사채", "신주인수권", "교환사채", "CB", "BW", "EB"]):
        funding_need = "메자닌/성장자금"
        risk_type = "희석·차환 리스크"
        action_stage = "구조 검토"
    elif any(k in event_text for k in ["회사채", "CP", "사채", "단기차입금", "차입", "리파이낸싱"]):
        funding_need = "차환/운전자금"
        risk_type = "차입·리파이낸싱 리스크"
        action_stage = "즉시 접촉" if risk_level in {"High", "Elevated"} else "우선 관찰"
    elif any(k in event_text for k in ["유형자산", "시설투자", "CAPEX", "증설", "공장"]):
        funding_need = "성장 CAPEX"
        risk_type = "투자집행/현금흐름 리스크"
        action_stage = "구조 검토"
    elif any(k in event_text for k in ["타법인", "출자증권", "인수", "합병", "M&A"]):
        funding_need = "인수/투자자금"
        risk_type = "인수금융/재무부담 리스크"
        action_stage = "우선 관찰"
    else:
        funding_need = SECTOR_DEFAULT_NEED.get(industry, "기초 모니터링")
        if industry == "건설/부동산":
            risk_type = "PF/우발채무 리스크"
        elif industry in {"석유화학/소재", "철강/금속", "운송/물류"}:
            risk_type = "업황/운전자본 리스크"
        elif industry in {"제약/바이오/헬스케어", "2차전지/전기차"}:
            risk_type = "성장투자/후속조달 리스크"
        elif industry == "금융":
            risk_type = "시장성 조달/자본비율 리스크"
        else:
            risk_type = "기초 신용 모니터링"
        action_stage = "정기 관찰" if risk_level in {"Low", "Watch"} else "우선 관찰"

    structure_group, recommended_structure, terms = _structure_group_and_terms(funding_need, risk_level, industry, event_text)
    if risk_level == "Critical":
        priority = "1. 긴급 확인"
    elif risk_level in {"High", "Elevated"} or event_score >= 85:
        priority = "2. 즉시 접촉"
    elif risk_level == "Moderate" or news_score >= 65:
        priority = "3. 구조 검토"
    elif risk_level == "Watch":
        priority = "4. 관심 관찰"
    else:
        priority = "5. 정기 모니터링"

    confidence = "높음" if event_score >= 75 or news_score >= 70 else "보통" if industry != "기타/미분류" else "낮음"
    if missing_fields and "detailed_dart_financial_deferred" in missing_fields:
        confidence = "보통" if confidence == "높음" else confidence

    factors = []
    if event_score:
        factors.append(f"최근 공시/이벤트 강도 {event_score}점 감지")
    factors.append(f"업종 기반 리스크 프로파일: {industry}")
    factors.append(f"자금수요 유형: {funding_need}")
    factors.append(f"위험유형: {risk_type}")
    factors.append(f"우선 검토 구조: {structure_group}")
    if confidence != "높음":
        factors.append(f"정보 신뢰도 {confidence}: 원문/재무 상세 보강 필요")

    if action_stage.startswith("Hold"):
        rationale = "부정적 신용 이벤트 가능성이 있어 단순 영업보다 원문 공시·감사의견·채권자 지위 확인이 선행되어야 합니다."
    elif event_score >= 75:
        rationale = f"최근 자금수요 관련 이벤트가 감지되어 {recommended_structure} 관점의 선제 접촉 후보입니다."
    elif risk_level in {"Elevated", "High"}:
        rationale = f"업종·뉴스·공시 신호상 {risk_type}가 높아 {structure_group} 중심의 구조화 검토가 필요합니다."
    else:
        rationale = f"현재는 {funding_need} 관점의 관찰 후보입니다. 신규 공시·실적 발표 시 접촉 우선순위를 재조정합니다."

    return {
        "risk_level": risk_level,
        "risk_score_internal": round(risk_numeric, 1),
        "risk_type": risk_type,
        "funding_need_type": funding_need,
        "structure_group": structure_group,
        "recommended_structure": recommended_structure,
        "suggested_terms": terms,
        "action_stage": action_stage,
        "priority": priority,
        "analysis_confidence": confidence,
        "rationale": rationale,
        "key_factors": factors,
    }

def risk_and_structure(rule_score: float, news_score: float, dart_event_score: int, missing_fields: List[str]) -> Tuple[str, str, str]:
    # Backward-compatible fallback. The main pipeline uses enhanced_finance_classification().
    level = "High" if dart_event_score >= 90 or news_score >= 90 else "Moderate" if rule_score >= 55 or news_score >= 70 else "Watch"
    return level, "구조화 금융 검토", "원문 공시 확인 후 조건 제시"


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


def normalize_phone(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def phone_to_tel_href(value: str) -> str:
    first = re.split(r"[,;/]", normalize_phone(value))[0].strip()
    cleaned = re.sub(r"[^0-9+]", "", first)
    digits = re.sub(r"\D", "", cleaned)
    if len(digits) < 7:
        return ""
    if cleaned.startswith("+"):
        cleaned = "+" + cleaned[1:].replace("+", "")
    else:
        cleaned = cleaned.replace("+", "")
    return f"tel:{cleaned}"


def normalize_external_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if not re.match(r"^https?://", text, flags=re.I):
        text = "https://" + text
    return text


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
        "phn_no": data.get("phn_no", ""),
        "adres": data.get("adres", ""),
        "hm_url": data.get("hm_url", ""),
        "ir_url": data.get("ir_url", ""),
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


def mock_rule_score(idx: int, event_severity: int, industry: str = "기타/미분류", issuer_seed: str = "") -> float:
    # Fast-mode proxy: sector risk + event signal + deterministic dispersion.
    # This is not a replacement for detailed financial statements; it is a first-pass screening score.
    sector = SECTOR_BASE_RISK.get(industry, 40)
    variation = stable_int(issuer_seed or str(idx), 21) - 10
    event_bonus = 18 if event_severity >= 90 else 12 if event_severity >= 75 else 6 if event_severity >= 60 else 0
    return round(clip(28 + sector * 0.45 + variation + event_bonus), 1)


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
        "credit_rating": "csv_pending",
        "llm_annotation": "transparent_proxy_static_mvp",
    }

    credit_rating_records, credit_rating_status = read_credit_rating_records()
    credit_rating_index = build_credit_rating_index(credit_rating_records)
    source_status_global["credit_rating"] = credit_rating_status

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
        news_cards = []
        news_cards.extend(dart_events.get("key_events", []))
        news_cards.extend(kind_events.get("key_events", []))
        rule_score = mock_rule_score(i, event_severity, industry, corp_code or corp_name)
        news_score = float(max(38 + stable_int(corp_code or corp_name, 20), event_severity)) if event_severity == 0 else float(event_severity)
        missing_fields = ["상세 재무제표 보강 필요"]
        classification = enhanced_finance_classification(corp_code or corp_name, industry, rule_score, news_score, event_severity, news_cards, missing_fields)
        ai_score = ai_base_from_signals(rule_score, news_score, event_severity, len(missing_fields))
        # Use risk score as a professional overlay so high-risk sectors/events are not all flattened into Low/Monitor.
        ai_score = round(clip(0.72 * ai_score + 0.28 * classification["risk_score_internal"]), 1)
        final_score = compute_final_funding_score(rule_score, ai_score, news_score)
        if not news_cards:
            news_cards = [{"title": f"최근 45일 직접 자금조달 이벤트 미검출 · {classification['funding_need_type']} 관점 정기 관찰", "source": "full_universe_fast_screen", "sentiment": "mixed", "severity": int(news_score)}]
        score_label = score_band(final_score)
        action_stage = classification["action_stage"] if str(classification["action_stage"]).startswith("Hold") else classification["priority"]
        credit_rating = credit_rating_for_issuer(row, credit_rating_index)
        ir_phone = normalize_phone(profile.get("phn_no", "")) if profile else ""
        ir_url = normalize_external_url(profile.get("ir_url", "")) if profile else ""
        company_homepage = normalize_external_url(profile.get("hm_url", "")) if profile else ""
        issuers.append({
            "rank": 0,
            "corp_name": corp_name,
            "corp_code": corp_code,
            "ticker": row.get("ticker", ""),
            "industry": industry,
            "industry_source": industry_source,
            "industry_code": str(profile.get("induty_code", "")) if profile else "",
            "company_address": str(profile.get("adres", "")) if profile else "",
            "ir_phone": ir_phone,
            "ir_phone_tel": phone_to_tel_href(ir_phone),
            "ir_phone_source": "OpenDART company.json phn_no" if ir_phone else "",
            "ir_url": ir_url,
            "company_homepage": company_homepage,
            "score_band": score_label,
            "trigger_type": trigger_type_from(event_severity, news_score, [n.get("title", "") for n in news_cards]),
            "priority": classification["priority"],
            "risk_level": classification["risk_level"],
            "risk_type": classification["risk_type"],
            "funding_need_type": classification["funding_need_type"],
            "structure_group": classification["structure_group"],
            "action_stage": action_stage,
            "action_comment": classification["action_stage"],
            "analysis_confidence": classification["analysis_confidence"],
            "final_score": final_score,
            "pure_financial_rule_score": round(rule_score, 1),
            "ai_base_score": ai_score,
            "news_trigger_score": round(news_score, 1),
            "recommended_structure": classification["recommended_structure"],
            "suggested_terms": classification["suggested_terms"],
            **credit_rating,
            "rationale": classification["rationale"],
            "key_factors": classification["key_factors"],
            "news": sorted(news_cards, key=lambda x: x.get("severity", 0), reverse=True)[:5],
            "source_status": {
                "dart_universe": source_status_global["dart_universe"],
                "dart_financial": source_status_global["dart_financial"],
                "dart_company_profile": source_status_global["dart_company_profile"],
                "dart_disclosure": source_status_global["dart_disclosure"],
                "news": source_status_global["news"],
                "kind_rss": source_status_global["kind_rss"],
                "rating": source_status_global["credit_rating"],
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
                merged_news = classified.get("key_news", []) + issuer.get("news", [])
                refreshed = enhanced_finance_classification(issuer.get("corp_code") or issuer.get("corp_name"), issuer.get("industry", "기타/미분류"), rule_score, news_score, event_severity, merged_news, issuer.get("missing_fields", []))
                ai_score = ai_base_from_signals(rule_score, news_score, event_severity, len(issuer.get("missing_fields", [])))
                ai_score = round(clip(0.72 * ai_score + 0.28 * refreshed["risk_score_internal"]), 1)
                final_score = compute_final_funding_score(rule_score, ai_score, news_score)
                action_stage = refreshed["action_stage"] if str(refreshed["action_stage"]).startswith("Hold") else refreshed["priority"]
                issuer.update({
                    "news_trigger_score": round(news_score, 1),
                    "ai_base_score": ai_score,
                    "final_score": final_score,
                    "priority": refreshed["priority"],
                    "score_band": score_band(final_score),
                    "trigger_type": trigger_type_from(event_severity, news_score, [n.get("title", "") for n in merged_news]),
                    "risk_level": refreshed["risk_level"],
                    "risk_type": refreshed["risk_type"],
                    "funding_need_type": refreshed["funding_need_type"],
                    "structure_group": refreshed["structure_group"],
                    "recommended_structure": refreshed["recommended_structure"],
                    "suggested_terms": refreshed["suggested_terms"],
                    "action_stage": action_stage,
                    "action_comment": refreshed["action_stage"],
                    "analysis_confidence": refreshed["analysis_confidence"],
                    "rationale": refreshed["rationale"],
                    "key_factors": refreshed["key_factors"],
                    "source_status": {**issuer["source_status"], "news": "live_ok_top_candidate"},
                })
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
            issuer["news"] = [{
                "title": f"상세 뉴스/공시 원문 저장은 우선순위 상위 {page_detail_limit}개 기업에 집중됩니다. 이 기업은 전체 스크리닝 기반의 요약 모니터링 정보로 표시되며, 신규 공시·뉴스·실적 이벤트 발생 시 상세 검토 대상으로 전환될 수 있습니다.",
                "source": "summary_screening",
                "sentiment": "neutral",
                "severity": int(issuer.get("news_trigger_score", 0) or 0),
            }]
            issuer["rule_breakdown"] = []
            issuer["rationale"] = f"{issuer.get('corp_name') or '해당 기업'}은 전체 상장사 스크리닝에 포함된 요약 모니터링 대상입니다. 현재는 상세 원문 저장보다 정기 관찰이 적합한 구간으로, 업종·자금수요·리스크 분류를 기준으로 관찰하고 신규 자금조달 공시나 실적 변화가 확인되면 접촉 우선순위를 재산정합니다."

    next_run = (t + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return {
        "as_of_date": t.strftime("%Y-%m-%d"),
        "policy_version": "v2.0-expert-risk-structure-segmentation",
        "service": {
            "name": "콜콜 (Cold Call)",
            "subtitle": "오늘 연락할 발행사를 콕 집어주는 플랫폼",
            "description": "오늘 연락할 발행사를 콕 집어주는 플랫폼. 전체 상장사를 업종·자금수요·리스크·금융구조 관점으로 세분화해 선제 영업 후보를 제시합니다.",
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
            "universe_mode": "opendart_full_listed_expert_segmentation_two_stage",
            "news_enrich_limit": news_limit,
            "page_detail_limit": page_detail_limit,
            "credit_rating_record_count": len(credit_rating_records),
            "ir_phone_mapped_count": sum(1 for x in issuers if x.get("ir_phone")),
            "industry_mapped_count": sum(1 for x in issuers if x.get("industry") != "기타/미분류"),
            "industry_unmapped_count": sum(1 for x in issuers if x.get("industry") == "기타/미분류"),
            "industry_source_counts": dict(Counter(x.get("industry_source", "unknown") for x in issuers)),
            "industry_mapping_method": "수기/CSV 표준화 → 키워드 보정 → DART 업종코드 → 일반 키워드 추정",
            "missing_fields": [],
            "notice": "공개정보 기반 자동 스냅샷입니다. 투자/영업 실행 전 원문 공시와 담당자 검토가 필요합니다.",
        },
        "kpis": {
            "screened_universe": len(rows),
            "priority_a": sum(1 for x in issuers if x.get("score_band") in {"S. 긴급 검토", "A. 즉시 접촉"}),
            "news_trigger_count": sum(1 for x in issuers if x.get("trigger_type") not in {"재무구조 점검", "기초 모니터링"}),
            "needs_review": sum(1 for x in issuers if x.get("risk_level") in {"Critical", "High", "Elevated"}),
            "rated_count": sum(1 for x in issuers if x.get("credit_rating_status") == "유효등급"),
        },
        "filters": {
            "industries": ordered_industry_filters(issuers),
            "risk_levels": ["전체", "Critical", "High", "Elevated", "Moderate", "Watch", "Low"],
            "priority_levels": ["전체", "1. 긴급 확인", "2. 즉시 접촉", "3. 구조 검토", "4. 관심 관찰", "5. 정기 모니터링"],
            "score_bands": ["전체", "S. 긴급 검토", "A. 즉시 접촉", "B. 구조화 검토", "C. 관심 관찰", "D. 정기 모니터링"],
            "trigger_types": ["전체", "신용/계속기업 리스크", "자금조달 직접공시", "차입/담보 이벤트", "투자·CAPEX 이벤트", "M&A/지분투자 이벤트", "업황/뉴스 압력", "재무구조 점검"],
            "risk_types": ["전체"] + sorted({x.get("risk_type", "") for x in issuers if x.get("risk_type")}),
            "funding_need_types": ["전체"] + sorted({x.get("funding_need_type", "") for x in issuers if x.get("funding_need_type")}),
            "structure_groups": ["전체"] + sorted({x.get("structure_group", "") for x in issuers if x.get("structure_group")}),
            "action_stages": ["전체", "1. 긴급 확인", "2. 즉시 접촉", "3. 구조 검토", "4. 관심 관찰", "5. 정기 모니터링", "Hold / 원문 확인"],
            "credit_rating_statuses": ["전체", "유효등급", "무등급"],
            "long_term_ratings": ["전체"] + sorted({x.get("long_term_rating", "") for x in issuers if x.get("long_term_rating") and x.get("long_term_rating") != "무등급"}),
            "short_term_ratings": ["전체"] + sorted({x.get("short_term_rating", "") for x in issuers if x.get("short_term_rating") and x.get("short_term_rating") != "무등급"}),
            "industry_sources": ["전체", "수기/CSV", "키워드 보정", "DART 업종코드", "키워드 추정", "자동 미분류"],
            "industry_categories": [{"name": k, "meaning": v} for k, v in INDUSTRY_CATEGORY_DESCRIPTIONS.items()],
            "definitions": [
                {"field": "업종", "meaning": "수기값을 우선 사용하고, 없으면 회사명 키워드와 DART 업종코드로 사용자용 범주에 자동 매핑합니다."},
                {"field": "우선순위", "meaning": "공시·뉴스·재무 신호를 종합해 영업 검토 순서를 나눈 값입니다. 산식은 화면에 노출하지 않습니다."},
                {"field": "Trigger", "meaning": "최근 자금조달 공시, 투자/차입 이벤트, 뉴스 신호, 기초 모니터링 중 어떤 신호가 우선 감지됐는지 표시합니다."},
                {"field": "Risk", "meaning": "Low/Watch/Moderate/Elevated/High/Critical로 세분화한 위험 수준입니다. 점수와 별도로 구조 검토에 사용합니다."},
                {"field": "자금수요 유형", "meaning": "차환, CAPEX, 메자닌, 자본확충, PF 등 예상되는 자금 목적입니다."},
                {"field": "추천 금융구조", "meaning": "공시·뉴스·업종 신호를 토대로 우선 검토할 수 있는 금융상품/구조입니다."},
                {"field": "신용등급", "meaning": "한국신용평가, NICE신용평가, 한국기업평가 기준 장기·단기 등급을 매칭합니다. 매칭값이 없으면 무등급으로 표시합니다."}
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
