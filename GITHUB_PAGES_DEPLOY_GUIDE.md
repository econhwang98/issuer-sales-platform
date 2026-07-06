"""발행사 선제 영업 플랫폼 scoring engine for static MVP.

Option C confirmed:
Final Funding Score = 45% Pure Financial Rule + 40% AI Base + 15% News Trigger.
News is excluded from Pure Financial Rule and handled separately.
Quick ratio threshold is confirmed as 100% 미만, not 10% 미만.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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

    # Confirmed by user: threshold is 100% 미만.
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
