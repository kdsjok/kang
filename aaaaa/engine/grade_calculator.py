"""Final grade calculation without an LLM client or API credentials."""
from typing import Tuple
from I_ACE.config.settings import get_settings


def calculate_final_grade(rule_grade: int, ai_grade: int, confidence: float, settings=None) -> Tuple[int, str]:
    """Combine Rule and AI grades using the configured confidence weights."""
    settings = settings if settings is not None else get_settings()
    if confidence >= settings.AI_CONFIDENCE_HIGH:
        weight_ai = settings.AI_WEIGHT_HIGH
        weight_desc = "AI 100%"
    elif confidence >= settings.AI_CONFIDENCE_MEDIUM:
        weight_ai = settings.AI_WEIGHT_MEDIUM
        weight_desc = "AI 70% + Rule 30%"
    else:
        weight_ai = 0.0
        weight_desc = "Rule 100% (AI 신뢰도 낮음)"

    # 가중 평균 (반올림)
    final_grade = round(rule_grade * (1 - weight_ai) + ai_grade * weight_ai)
    final_grade = max(1, min(5, final_grade))  # 1~5 범위 보장

    # 최종 판정 사유 생성
    if weight_ai > 0:
        final_reason = f"[{weight_desc}] Rule({rule_grade}) + AI({ai_grade}) → 최종({final_grade})"
    else:
        final_reason = f"[{weight_desc}] Rule({rule_grade}) → 최종({final_grade})"

    return final_grade, final_reason
