{
  "as_of_date": "2026-07-06",
  "policy_version": "v1.1-static-mvp-option-c",
  "service": {
    "name": "발행사 선제 영업 플랫폼",
    "subtitle": "공개정보 기반 자금수요 레이더",
    "description": "DART, 뉴스, 재무데이터를 기반으로 향후 자금조달 필요성이 높은 발행사를 선제 탐지합니다."
  },
  "formula": {
    "label": "Final Funding Score",
    "rule_weight": 0.45,
    "ai_weight": 0.4,
    "news_weight": 0.15,
    "text": "Final = 45% × Pure Financial Rule + 40% × AI Base + 15% × News Trigger"
  },
  "pipeline_status": {
    "last_run_kst": "2026-07-06 09:58:10",
    "next_run_kst": "2026-07-07 07:00:00",
    "source_status": {
      "dart": "api_key_missing",
      "news": "api_key_missing",
      "credit_rating": "manual_file_needed",
      "llm_annotation": "sample_placeholder"
    },
    "missing_fields": [],
    "notice": "공개정보 기반 자동 스냅샷입니다. 투자/영업 실행 전 원문 공시와 담당자 검토가 필요합니다."
  },
  "kpis": {
    "screened_universe": 5,
    "priority_a": 2,
    "news_trigger_count": 5,
    "needs_review": 5
  },
  "filters": {
    "industries": [
      "전체",
      "건설",
      "바이오",
      "소재",
      "전자부품"
    ],
    "risk_levels": [
      "전체",
      "Low",
      "Mid",
      "High"
    ],
    "priority_levels": [
      "전체",
      "Priority A",
      "Watchlist B",
      "Monitor C"
    ]
  },
  "issuers": [
    {
      "rank": 1,
      "corp_name": "신세계건설",
      "corp_code": "00217947",
      "ticker": "034300",
      "industry": "건설",
      "priority": "Priority A",
      "risk_level": "Mid",
      "final_score": 82.5,
      "pure_financial_rule_score": 78.0,
      "ai_base_score": 84.0,
      "news_trigger_score": 92.0,
      "recommended_structure": "CP + 담보부 라인",
      "suggested_terms": "9M · 6.40% 내외",
      "rationale": "자동 수집된 공개정보와 정책 v1.1 기준으로 산출된 후보입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
      "key_factors": [
        "공개정보 기반",
        "Option C 산식",
        "뉴스 Trigger 분리",
        "Risk Gate 별도 적용"
      ],
      "news": [
        {
          "title": "테스트 모드: API 키 미설정으로 예시 뉴스 사용",
          "source": "sample",
          "sentiment": "mixed",
          "severity": 92
        }
      ],
      "source_status": {
        "dart": "api_key_missing",
        "news": "api_key_missing",
        "rating": "manual_file_needed"
      },
      "missing_fields": [
        "live_news_api",
        "parsed_dart_metrics"
      ]
    },
    {
      "rank": 2,
      "corp_name": "서진시스템",
      "corp_code": "00838005",
      "ticker": "178320",
      "industry": "전자부품",
      "priority": "Priority A",
      "risk_level": "Low",
      "final_score": 74.5,
      "pure_financial_rule_score": 66.0,
      "ai_base_score": 79.0,
      "news_trigger_score": 88.0,
      "recommended_structure": "Credit Loan / 운영자금 라인",
      "suggested_terms": "1Y · 변동금리 + 약정",
      "rationale": "자동 수집된 공개정보와 정책 v1.1 기준으로 산출된 후보입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
      "key_factors": [
        "공개정보 기반",
        "Option C 산식",
        "뉴스 Trigger 분리",
        "Risk Gate 별도 적용"
      ],
      "news": [
        {
          "title": "테스트 모드: API 키 미설정으로 예시 뉴스 사용",
          "source": "sample",
          "sentiment": "mixed",
          "severity": 88
        }
      ],
      "source_status": {
        "dart": "api_key_missing",
        "news": "api_key_missing",
        "rating": "manual_file_needed"
      },
      "missing_fields": [
        "live_news_api",
        "parsed_dart_metrics"
      ]
    },
    {
      "rank": 3,
      "corp_name": "코오롱인더스트리",
      "corp_code": "00795135",
      "ticker": "120110",
      "industry": "소재",
      "priority": "Watchlist B",
      "risk_level": "Mid",
      "final_score": 63.5,
      "pure_financial_rule_score": 61.0,
      "ai_base_score": 65.0,
      "news_trigger_score": 67.0,
      "recommended_structure": "만기/금리 모니터링 후 제안",
      "suggested_terms": "Review after next filing",
      "rationale": "자동 수집된 공개정보와 정책 v1.1 기준으로 산출된 후보입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
      "key_factors": [
        "공개정보 기반",
        "Option C 산식",
        "뉴스 Trigger 분리",
        "Risk Gate 별도 적용"
      ],
      "news": [
        {
          "title": "테스트 모드: API 키 미설정으로 예시 뉴스 사용",
          "source": "sample",
          "sentiment": "mixed",
          "severity": 67
        }
      ],
      "source_status": {
        "dart": "api_key_missing",
        "news": "api_key_missing",
        "rating": "manual_file_needed"
      },
      "missing_fields": [
        "live_news_api",
        "parsed_dart_metrics"
      ]
    },
    {
      "rank": 4,
      "corp_name": "롯데케미칼",
      "corp_code": "00105271",
      "ticker": "011170",
      "industry": "소재",
      "priority": "Watchlist B",
      "risk_level": "Mid",
      "final_score": 61.9,
      "pure_financial_rule_score": 58.0,
      "ai_base_score": 64.0,
      "news_trigger_score": 68.0,
      "recommended_structure": "회사채/CP 시장 모니터링",
      "suggested_terms": "시장성 조달 조건 확인",
      "rationale": "자동 수집된 공개정보와 정책 v1.1 기준으로 산출된 후보입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
      "key_factors": [
        "공개정보 기반",
        "Option C 산식",
        "뉴스 Trigger 분리",
        "Risk Gate 별도 적용"
      ],
      "news": [
        {
          "title": "테스트 모드: API 키 미설정으로 예시 뉴스 사용",
          "source": "sample",
          "sentiment": "mixed",
          "severity": 68
        }
      ],
      "source_status": {
        "dart": "api_key_missing",
        "news": "api_key_missing",
        "rating": "manual_file_needed"
      },
      "missing_fields": [
        "live_news_api",
        "parsed_dart_metrics"
      ]
    },
    {
      "rank": 5,
      "corp_name": "HLB",
      "corp_code": "00358995",
      "ticker": "028300",
      "industry": "바이오",
      "priority": "Monitor C",
      "risk_level": "High",
      "final_score": 53.6,
      "pure_financial_rule_score": 54.0,
      "ai_base_score": 50.0,
      "news_trigger_score": 62.0,
      "recommended_structure": "Other / Manual Review",
      "suggested_terms": "투자심의 전 별도 검토",
      "rationale": "자동 수집된 공개정보와 정책 v1.1 기준으로 산출된 후보입니다. 실행 전 원문 공시와 담당자 검토가 필요합니다.",
      "key_factors": [
        "공개정보 기반",
        "Option C 산식",
        "뉴스 Trigger 분리",
        "Risk Gate 별도 적용"
      ],
      "news": [
        {
          "title": "테스트 모드: API 키 미설정으로 예시 뉴스 사용",
          "source": "sample",
          "sentiment": "mixed",
          "severity": 62
        }
      ],
      "source_status": {
        "dart": "api_key_missing",
        "news": "api_key_missing",
        "rating": "manual_file_needed"
      },
      "missing_fields": [
        "live_news_api",
        "parsed_dart_metrics"
      ]
    }
  ]
}