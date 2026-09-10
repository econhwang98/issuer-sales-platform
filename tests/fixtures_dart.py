# -*- coding: utf-8 -*-
"""OpenDART fnlttSinglAcntAll 합성 응답.

API 키 없이 재무 파서와 점수 교체 단계를 검증하기 위한 고정 데이터다.
금액은 원 단위이며, 억원으로 환산했을 때 계산이 눈으로 검산되는 값으로 골랐다.
"""


def row(sj, aid, nm, th, fr=None, bf=None, add=None):
    r = {"sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": th}
    if fr is not None:
        r["frmtrm_amount"] = fr
    if bf is not None:
        r["bfefrmtrm_amount"] = bf
    if add is not None:
        r["thstrm_add_amount"] = add
    return r


# 사업보고서: 당기·전기·전전기 3개년이 함께 온다.
ANNUAL = [
    row("BS", "ifrs-full_Assets", "자산총계", "1,000,000,000,000", "900,000,000,000", "800,000,000,000"),
    row("BS", "ifrs-full_Liabilities", "부채총계", "600,000,000,000", "540,000,000,000", "480,000,000,000"),
    row("BS", "ifrs-full_Equity", "자본총계", "400,000,000,000", "360,000,000,000", "320,000,000,000"),
    row("BS", "ifrs-full_CurrentAssets", "유동자산", "300,000,000,000", "280,000,000,000", "260,000,000,000"),
    row("BS", "ifrs-full_CurrentLiabilities", "유동부채", "350,000,000,000", "300,000,000,000", "250,000,000,000"),
    row("BS", "ifrs-full_Inventories", "재고자산", "120,000,000,000", "110,000,000,000", "100,000,000,000"),
    row("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "50,000,000,000", "45,000,000,000", "40,000,000,000"),
    row("BS", "", "단기차입금", "150,000,000,000", "130,000,000,000", "110,000,000,000"),
    row("BS", "", "장기차입금", "200,000,000,000", "180,000,000,000", "160,000,000,000"),
    row("BS", "", "사채", "100,000,000,000", "90,000,000,000", "80,000,000,000"),
    row("IS", "ifrs-full_Revenue", "수익(매출액)", "800,000,000,000", "750,000,000,000", "700,000,000,000"),
    row("IS", "dart_OperatingIncomeLoss", "영업이익", "40,000,000,000", "35,000,000,000", "(5,000,000,000)"),
    row("IS", "ifrs-full_ProfitLoss", "당기순이익", "20,000,000,000", "18,000,000,000", "(9,000,000,000)"),
    row("IS", "", "이자비용", "20,000,000,000", "18,000,000,000", "16,000,000,000"),
    row("CF", "", "감가상각비", "30,000,000,000", "28,000,000,000", "26,000,000,000"),
    row("CF", "", "무형자산상각비", "5,000,000,000", "4,000,000,000", "3,000,000,000"),
    row("CF", "", "영업활동현금흐름", "60,000,000,000", "55,000,000,000", "(2,000,000,000)"),
    row("CF", "", "투자활동현금흐름", "(80,000,000,000)", "(70,000,000,000)", "(60,000,000,000)"),
    row("CF", "", "현금및현금성자산의순증가", "(10,000,000,000)", "5,000,000,000", "(3,000,000,000)"),
]

# 반기보고서: 손익은 누적치(thstrm_add_amount)로 오고, 유동자산·이자비용·현금흐름이 빠져 있다.
# 분기에 없는 항목을 직전 연간으로 되메우는 동작을 시험하기 위한 구성이다.
QUARTER = [
    row("BS", "ifrs-full_Assets", "자산총계", "1,050,000,000,000"),
    row("BS", "ifrs-full_Liabilities", "부채총계", "640,000,000,000"),
    row("BS", "ifrs-full_Equity", "자본총계", "410,000,000,000"),
    row("BS", "", "단기차입금", "160,000,000,000"),
    row("BS", "", "장기차입금", "210,000,000,000"),
    row("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "55,000,000,000"),
    row("IS", "ifrs-full_Revenue", "매출액", "220,000,000,000", add="430,000,000,000"),
    row("IS", "dart_OperatingIncomeLoss", "영업이익", "12,000,000,000", add="23,000,000,000"),
    row("CF", "", "감가상각비", "0", add="18,000,000,000"),
]


def make_fake_api(counter=None):
    """financial_engine.build_financial_shard에 주입할 가짜 API."""
    def fake_api(path, params):
        if counter is not None:
            counter["n"] = counter.get("n", 0) + 1
        if params.get("fs_div") == "CFS" and params.get("reprt_code") == "11011":
            if params.get("bsns_year") in {"2025", "2022"}:
                return {"status": "000", "list": ANNUAL}
        if params.get("fs_div") == "CFS" and params.get("reprt_code") == "11012":
            return {"status": "000", "list": QUARTER}
        return {"status": "013", "message": "no data"}
    return fake_api
