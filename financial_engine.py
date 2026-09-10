#!/usr/bin/env python3
"""OpenDART 재무제표 수집·파싱 모듈.

두 가지를 만든다.

1. 기업 검토보고서가 읽는 `financials/{corp_code}.json` 샤드
   (스키마는 financials/README.md 참고)
2. scoring_engine.compute_pure_financial_rule_score에 넣을 FinancialMetrics

`fnlttSinglAcntAll`은 한 번 호출하면 당기·전기·전전기 3개년을 함께 돌려준다.
따라서 기업당 연간 2콜 + 최신분기 1콜이면 5개년 + 최신분기가 채워진다.

HTTP는 호출자가 주입한다(`api_get`). 파싱 로직을 API 키 없이 시험할 수 있게 하려는
분리이며, 이 모듈 자체는 requests에 의존하지 않는다.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

# 억원
UNIT_DIVISOR = 100_000_000
UNIT_LABEL = "억원"

ANNUAL_REPRT = "11011"


def quarter_candidates_for(month: int) -> List[Tuple[str, str, int]]:
    """조회 시점의 월에 맞는 분기보고서 후보를 (보고서코드, 표기, 연도오프셋)로 준다.

    분기·반기 보고서는 분기 종료 후 45일 안에 제출된다(1분기 5월 중순, 반기 8월 중순,
    3분기 11월 중순). 아직 제출되지 않은 보고서를 찍어보면 호출만 낭비되므로,
    월을 보고 나올 법한 것부터 고른다. 후보는 최대 2개만 둔다.

    연도오프셋 0은 직전 사업연도의 다음 해(=진행 중인 사업연도)를 뜻한다.
    """
    if month >= 12:
        return [("11014", "3분기", 0), ("11012", "반기", 0)]
    if month >= 9:
        return [("11012", "반기", 0), ("11013", "1분기", 0)]
    if month >= 6:
        return [("11013", "1분기", 0), ("11014", "3분기", -1)]
    if month >= 4:
        return [("11014", "3분기", -1), ("11012", "반기", -1)]
    # 1~3월은 진행 사업연도의 분기보고서가 아직 없다.
    return [("11014", "3분기", -1)]

# 기간별 금액 필드. 분기 손익은 누적치(thstrm_add_amount)를 먼저 본다.
PERIOD_AMOUNT_KEYS = {
    "thstrm": ["thstrm_amount"],
    "frmtrm": ["frmtrm_amount"],
    "bfefrmtrm": ["bfefrmtrm_amount"],
}
PERIOD_FLOW_KEYS = {
    "thstrm": ["thstrm_add_amount", "thstrm_amount"],
    "frmtrm": ["frmtrm_add_amount", "frmtrm_amount"],
    "bfefrmtrm": ["bfefrmtrm_amount"],
}
PERIOD_YEAR_OFFSET = {"thstrm": 0, "frmtrm": -1, "bfefrmtrm": -2}

# 잔액 계정(BS)인지 기간 계정(IS/CF)인지에 따라 읽는 필드가 다르다.
BALANCE_STATEMENTS = {"BS"}
FLOW_STATEMENTS = {"IS", "CIS", "CF"}

# account_id(IFRS 표준 태그)를 먼저 보고, 없으면 계정명으로 맞춘다.
# 계정명은 회사마다 띄어쓰기가 달라 공백을 제거하고 비교한다.
ACCOUNT_MATCHERS: Dict[str, Tuple[List[str], List[str], set]] = {
    "revenue": (
        ["ifrs-full_Revenue", "ifrs_Revenue"],
        ["매출액", "수익(매출액)", "영업수익", "매출"],
        {"IS", "CIS"},
    ),
    "operating_income": (
        ["dart_OperatingIncomeLoss", "ifrs-full_ProfitLossFromOperatingActivities"],
        ["영업이익", "영업이익(손실)", "영업손익"],
        {"IS", "CIS"},
    ),
    "net_income": (
        ["ifrs-full_ProfitLoss"],
        ["당기순이익", "당기순이익(손실)", "당기순손익", "분기순이익", "반기순이익"],
        {"IS", "CIS"},
    ),
    "interest_expense": ([], ["이자비용", "금융원가", "금융비용"], {"IS", "CIS"}),
    "total_assets": (["ifrs-full_Assets"], ["자산총계"], {"BS"}),
    "total_liabilities": (["ifrs-full_Liabilities"], ["부채총계"], {"BS"}),
    "equity": (["ifrs-full_Equity"], ["자본총계"], {"BS"}),
    "current_assets": (["ifrs-full_CurrentAssets"], ["유동자산"], {"BS"}),
    "current_liabilities": (["ifrs-full_CurrentLiabilities"], ["유동부채"], {"BS"}),
    "inventory": (["ifrs-full_Inventories"], ["재고자산"], {"BS"}),
    "cash": (
        ["ifrs-full_CashAndCashEquivalents"],
        ["현금및현금성자산", "현금및현금성자산등"],
        {"BS"},
    ),
    "depreciation": ([], ["감가상각비"], {"CF"}),
    "amortization": ([], ["무형자산상각비", "무형자산상각", "사용권자산상각비"], {"CF"}),
    "operating_cash_flow": (
        [],
        ["영업활동현금흐름", "영업활동으로인한현금흐름", "영업활동순현금흐름"],
        {"CF"},
    ),
    "investing_cash_flow": (
        [],
        ["투자활동현금흐름", "투자활동으로인한현금흐름", "투자활동순현금흐름"],
        {"CF"},
    ),
    "financing_cash_flow": (
        [],
        ["재무활동현금흐름", "재무활동으로인한현금흐름", "재무활동순현금흐름"],
        {"CF"},
    ),
    "net_cash_change": (
        [],
        ["현금및현금성자산의순증가", "현금및현금성자산의증가", "현금및현금성자산의순증감",
         "현금및현금성자산의증가(감소)", "현금및현금성자산의순증가(감소)"],
        {"CF"},
    ),
}

# 총차입금은 표준 태그가 없어 계정명을 더해 구한다.
DEBT_ACCOUNT_NAMES = [
    "단기차입금", "유동성장기부채", "유동성장기차입금", "유동성사채", "유동성전환사채",
    "사채", "장기차입금", "전환사채", "신주인수권부사채", "교환사채",
    "유동리스부채", "비유동리스부채", "리스부채",
]


def _squash(text: Any) -> str:
    return str(text or "").replace(" ", "").replace("　", "").strip()


_DEBT_NAMES_SQUASHED = {_squash(n) for n in DEBT_ACCOUNT_NAMES}
_MATCHERS_SQUASHED = {
    field: (ids, {_squash(n) for n in names}, statements)
    for field, (ids, names, statements) in ACCOUNT_MATCHERS.items()
}


def to_number(value: Any) -> Optional[float]:
    """DART 금액 문자열을 숫자로. 빈 값과 '-'는 None, 괄호는 음수로 읽는다."""
    text = str(value if value is not None else "").replace(",", "").strip()
    if not text or text in {"-", "--", "*"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    if text.startswith("△") or text.startswith("▲"):
        negative = True
        text = text[1:].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return -number if negative else number


def _pick(row: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        value = to_number(row.get(key))
        if value is not None:
            return value
    return None


def parse_period(rows: List[Dict[str, Any]], period: str) -> Dict[str, Optional[float]]:
    """fnlttSinglAcntAll 응답에서 한 기간(당기/전기/전전기)의 계정을 뽑는다."""
    out: Dict[str, Optional[float]] = {field: None for field in ACCOUNT_MATCHERS}
    debt_total = 0.0
    debt_found = False
    for row in rows:
        statement = str(row.get("sj_div") or "").strip().upper()
        keys = PERIOD_FLOW_KEYS[period] if statement in FLOW_STATEMENTS else PERIOD_AMOUNT_KEYS[period]
        amount = _pick(row, keys)
        if amount is None:
            continue
        account_id = str(row.get("account_id") or "").strip()
        account_nm = _squash(row.get("account_nm"))
        for field, (ids, names, statements) in _MATCHERS_SQUASHED.items():
            if out[field] is not None or statement not in statements:
                continue
            if (account_id and account_id in ids) or account_nm in names:
                out[field] = amount
        if statement in BALANCE_STATEMENTS and account_nm in _DEBT_NAMES_SQUASHED:
            debt_total += amount
            debt_found = True
    out["total_debt"] = debt_total if debt_found else None
    return out


def _ratio(numerator: Optional[float], denominator: Optional[float], scale: float = 1.0) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * scale, 1)


def _scaled(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value / UNIT_DIVISOR, 0)


def derive_shard_row(period_label: str, accounts: Dict[str, Optional[float]]) -> Dict[str, Any]:
    """검토보고서 재무 표에 그대로 들어가는 한 열을 만든다."""
    ebit = accounts.get("operating_income")
    depreciation = accounts.get("depreciation") or 0.0
    amortization = accounts.get("amortization") or 0.0
    ebitda = None if ebit is None else ebit + depreciation + amortization
    total_debt = accounts.get("total_debt")
    cash = accounts.get("cash")
    net_debt = None if total_debt is None else total_debt - (cash or 0.0)
    revenue = accounts.get("revenue")
    interest = accounts.get("interest_expense")

    # EBITDA가 0 이하이면 배수 지표는 의미가 없다. 억지로 계산하지 않고 비운다.
    net_debt_to_ebitda = None
    ebitda_to_interest = None
    if ebitda is not None and ebitda > 0:
        if net_debt is not None:
            net_debt_to_ebitda = round(net_debt / ebitda, 1)
        if interest:
            ebitda_to_interest = round(ebitda / abs(interest), 1)

    return {
        "period": period_label,
        "revenue": _scaled(revenue),
        "ebit": _scaled(ebit),
        "ebitda": _scaled(ebitda),
        "net_income": _scaled(accounts.get("net_income")),
        "total_assets": _scaled(accounts.get("total_assets")),
        "total_debt": _scaled(total_debt),
        "net_debt": _scaled(net_debt),
        "ebit_margin": _ratio(ebit, revenue, 100),
        "ebitda_margin": _ratio(ebitda, revenue, 100),
        "ebitda_to_interest": ebitda_to_interest,
        "net_debt_to_ebitda": net_debt_to_ebitda,
        "debt_ratio": _ratio(accounts.get("total_liabilities"), accounts.get("equity"), 100),
        "debt_dependency": _ratio(total_debt, accounts.get("total_assets"), 100),
    }


def to_financial_metrics(accounts: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
    """scoring_engine.FinancialMetrics에 그대로 넣을 수 있는 값들."""
    current_assets = accounts.get("current_assets")
    current_liabilities = accounts.get("current_liabilities")
    inventory = accounts.get("inventory")
    quick_assets = None
    if current_assets is not None:
        quick_assets = current_assets - (inventory or 0.0)
    ebit = accounts.get("operating_income")
    interest = accounts.get("interest_expense")
    return {
        "debt_to_equity_pct": _ratio(accounts.get("total_liabilities"), accounts.get("equity"), 100),
        "current_ratio_pct": _ratio(current_assets, current_liabilities, 100),
        "quick_ratio_pct": _ratio(quick_assets, current_liabilities, 100),
        "debt_dependence_pct": _ratio(accounts.get("total_debt"), accounts.get("total_assets"), 100),
        "interest_coverage": None if not interest else _ratio(ebit, abs(interest)),
        "operating_cash_flow": accounts.get("operating_cash_flow"),
        "investing_cash_flow": accounts.get("investing_cash_flow"),
        "net_cash_change": accounts.get("net_cash_change"),
    }


def _rows_of(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    if str(payload.get("status") or "") not in {"000", "", "None"}:
        return []
    return payload.get("list") or []


def build_financial_shard(
    corp_code: str,
    corp_name: str,
    latest_year: int,
    api_get: Callable[[str, Dict[str, Any]], Dict[str, Any]],
    fetched_at: str = "",
    as_of_month: int = 12,
) -> Optional[Dict[str, Any]]:
    """기업 하나의 재무 샤드를 만든다. 유효한 기간이 하나도 없으면 None.

    연결(CFS)을 먼저 시도하고, 연결재무제표가 없는 회사는 별도(OFS)로 되돌아간다.
    """
    annual_rows: List[Tuple[str, Dict[str, Optional[float]]]] = []
    fs_used = ""

    for fs_div in ("CFS", "OFS"):
        annual_rows = []
        # 한 번에 3개년이 오므로 두 번이면 5개년을 덮는다.
        for base_year in (latest_year, latest_year - 3):
            payload = api_get("fnlttSinglAcntAll.json", {
                "corp_code": corp_code,
                "bsns_year": str(base_year),
                "reprt_code": ANNUAL_REPRT,
                "fs_div": fs_div,
            })
            rows = _rows_of(payload)
            if not rows:
                continue
            for period, offset in PERIOD_YEAR_OFFSET.items():
                year = base_year + offset
                if year > latest_year or year <= latest_year - 5:
                    continue
                accounts = parse_period(rows, period)
                if accounts.get("total_assets") is None and accounts.get("revenue") is None:
                    continue
                annual_rows.append((f"{year}(12)", accounts))
        if annual_rows:
            fs_used = fs_div
            break

    if not annual_rows:
        return None

    # 같은 연도가 두 호출에서 겹칠 수 있다. 오래된 순으로 두고 중복은 하나만 남긴다.
    by_period: Dict[str, Dict[str, Optional[float]]] = {}
    for label, accounts in annual_rows:
        by_period.setdefault(label, accounts)
    ordered = [(label, by_period[label]) for label in sorted(by_period)]

    quarter_row = None
    quarter_accounts = None
    for reprt_code, label, year_offset in quarter_candidates_for(as_of_month):
        quarter_year = latest_year + 1 + year_offset
        payload = api_get("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(quarter_year),
            "reprt_code": reprt_code,
            "fs_div": fs_used,
        })
        rows = _rows_of(payload)
        if not rows:
            continue
        accounts = parse_period(rows, "thstrm")
        if accounts.get("total_assets") is None and accounts.get("revenue") is None:
            continue
        quarter_accounts = accounts
        quarter_row = derive_shard_row(f"{quarter_year} {label}", accounts)
        break

    # 분기 보고서는 유동자산·이자비용·현금흐름을 싣지 않는 경우가 잦다.
    # 분기에 없는 항목은 직전 연간 값으로 되메워야 룰 점수 근거가 통째로 비지 않는다.
    latest_annual = ordered[-1][1]
    if quarter_accounts:
        latest_accounts = {
            field: (quarter_accounts.get(field)
                    if quarter_accounts.get(field) is not None
                    else latest_annual.get(field))
            for field in set(quarter_accounts) | set(latest_annual)
        }
    else:
        latest_accounts = latest_annual
    return {
        "corp_code": corp_code,
        "corp_name": corp_name,
        "unit": UNIT_LABEL,
        "latest_period": (quarter_row or derive_shard_row(*ordered[-1]))["period"],
        "source": f"OpenDART fnlttSinglAcntAll ({fs_used})",
        "fetched_at": fetched_at,
        "annual": [derive_shard_row(label, accounts) for label, accounts in ordered],
        "quarter": quarter_row,
        # 차입금 만기구조는 재무제표 본문이 아니라 주석에 있어 이 API로는 못 뽑는다.
        # 샤드에서 생략하면 검토보고서에서 만기구조 표가 빠진다.
        "metrics": to_financial_metrics(latest_accounts),
    }
