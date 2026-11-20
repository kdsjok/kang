"""
품질 등급 AI 분석기 (Qwen LLM)
- Rule Base 로직 보완
- QwenChatModel(Qwen36-27b)을 활용한 AI 분석
- 하이브리드 최종 등급 산출
"""

import os
import re
import json
import time
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

from I_ACE.config.settings import get_settings
from .grade_calculator import calculate_final_grade


@dataclass
class AIAnalysisResult:
    """AI 분석 결과"""
    suggested_grade: int
    confidence: float
    reasoning: str


class QualityAIAnalyzer:
    """
    품질 등급 AI 분석기
    - Qwen36-27b LLM 기반 품질 등급 분석
    - 병렬 처리 지원
    """

    def __init__(self, settings=None):
        """
        초기화

        Args:
            settings: 설정 객체
        """
        self.settings = settings or get_settings()
        self.settings.setup_environment()

        # Qwen LLM 초기화 (OpenAI 클라이언트)
        vlm_base_url = os.getenv('VLM_BASE_URL')
        vlm_api_key = os.getenv('VLM_API')
        if not vlm_base_url or not vlm_api_key:
            raise RuntimeError("VLM_BASE_URL 또는 VLM_API 환경 변수가 설정되어 있지 않습니다.")

        self.qwen_client = OpenAI(
            base_url=vlm_base_url,
            api_key=vlm_api_key,
        )
        self.model_name = "qwen3.8-27b-fp8"
        
        self.system_prompt = """당신은 삼성전자 품질관리 전문가 AI입니다.
자재의 다차원 품질 데이터를 종합 분석하여 최적의 품질 등급을 판정해야 합니다.

[등급 기준]
- 1등급: 최우수 품질 → 입고검사 수량 감소 권장
- 2등급: 우수 품질 → 입고검사 수량 감소 권장
- 3등급: 보통 품질 → 현행 유지
- 4등급: 품질 저하 → 입고검사 횟수 증가 필요
- 5등급: 심각 품질 문제 → 입고검사 횟수 대폭 증가 필요

[분석 고려사항]
1. 시계열 추세: 전기간 → 3~6개월 → 1~2주 품질 변화 패턴
2. PPM과 불량률의 상관관계 및 이상치 여부
3. 공정 이상 신호(CPK_AB, EDGE_AB, AB_CNT) 영향도
4. MQIS 트랜드 이탈(LEAVE_CNT) 의미 해석
5. 입고검사 vs 공정 품질 연계성
6. 카테고리별 특성 고려 (GLASS, 기구, APS 등)
7. 데이터 I/F 신뢰성(IF_CD) 고려

반드시 아래 JSON 형식으로만 응답하세요:
{{"suggested_grade": int(1~5), "confidence": float(0.0~1.0), "reasoning": "판정 근거 설명"}}"""
    
    def create_analysis_prompt(self, row: dict) -> str:
        """
        품질 분석 프롬프트 생성
        
        Args:
            row: 데이터 행 (딕셔너리)
        
        Returns:
            분석 프롬프트 문자열
        """
        return f"""[자재 정보]
- 부품명: {row.get('PART_NM', 'N/A')}
- 부품코드: {row.get('PART_CD', 'N/A')}
- 대분류: {row.get('CATEGORY_L', 'N/A')}
- 소분류: {row.get('CATEGORY_S', 'N/A')}
- 협력사명: {row.get('VEND_NM', 'N/A')}
- 협력사코드: {row.get('VEND_L_CD', 'N/A')}

[전기간 품질 지표]
- 총 입고 LOT 수: {row.get('TOTAL_LOT_CNT', 0)}
- 총 입고시료 수: {row.get('TOTAL_GR_QTY', 0)}
- 총 검사수: {row.get('TOTAL_INSP_QTY', 0)}
- 불량검사 수: {row.get('TOTAL_INSP_DEF_QTY', 0)}
- 불량검사 불량률(PPM): {row.get('TOTAL_INSP_PPM', 0)}
- LOT 불량률(%): {row.get('TOTAL_FAIL_RATE', 0)}

[3~6개월 품질 지표]
- 입고 LOT 수: {row.get('MONTH_LOT_CNT', 0)}
- 총 입고시료 수: {row.get('MONTH_GR_QTY', 0)}
- 총 검사수: {row.get('MONTH_INSP_QTY', 0)}
- 불량검사 수: {row.get('MONTH_INSP_DEF_QTY', 0)}
- 불량검사 불량률(PPM): {row.get('MONTH_INSP_PPM', 0)}
- LOT 불량률(%): {row.get('MONTH_FAIL_RATE', 0)}

[1~2주 품질 지표]
- 입고 LOT 수: {row.get('WEEK_LOT_CNT', 0)}
- 총 입고시료 수: {row.get('WEEK_GR_QTY', 0)}
- 총 검사수: {row.get('WEEK_INSP_QTY', 0)}
- 불량검사 수: {row.get('WEEK_INSP_DEF_QTY', 0)}
- 불량검사 불량률(PPM): {row.get('WEEK_INSP_PPM', 0)}
- LOT 불량률(%): {row.get('WEEK_FAIL_RATE', 0)}

[이상 감지 지표]
- Cpk 이상값지 수: {row.get('CPK_AB', 0)}
- Edge 이상값지 수: {row.get('EDGE_AB', 0)}
- 이상값지 수(AB_CNT): {row.get('AB_CNT', 0)}
- MQIS 트랜드 이탈 수: {row.get('LEAVE_CNT', 0)}

[공정 품질 데이터]
- 3~6개월 불량 수: {row.get('defect_qty_MONTH', 0)}
- 3~6개월 생산수: {row.get('ACRS_QTY_MONTH', 0)}
- 3~6개월 불량률(PPM): {row.get('PRODC_PPM_MONTH', 0)}
- 1~2주 불량 수: {row.get('defect_qty_WEEK', 0)}
- 1~2주 생산수: {row.get('ACRS_QTY_WEEK', 0)}
- 1~2주 불량률(PPM): {row.get('PRODC_PPM_WEEK', 0)}
- PPM 트렌드 비율: {row.get('ppm_trend_ratio', 0)}배

[데이터 신뢰성]
- 데이터 IF 코드: {row.get('IF_CD', 0)}

[Rule Base 1차 판정 결과]
- 전기간 등급: {row.get('TOTAL_LEVEL', 'N/A')}
- 3~6개월 등급: {row.get('MONTH_LEVEL', 'N/A')}
- 1~2주 등급: {row.get('WEEK_LEVEL', 'N/A')}
- 1차 판정 등급: {row.get('PART_Q_LEVEL', 'N/A')}
- 1차 판정 사유: {row.get('LEVEL_REASON', 'N/A')}

[분석 요청]
위 데이터를 종합 분석하여 최종 품질 등급을 제안해주세요.
특히 다음 사항을 고려해주세요:
1. 단기(1~2주) 품질이 장기 대비 급격히 변화했는지
2. 공정 이상 신호가 품질에 미치는 영향
3. 입고검사와 공정 품질의 일관성
4. Rule Base 판정 결과와의 비교 분석

반드시 JSON 형식으로만 응답하세요:
{{"suggested_grade": int(1~5), "confidence": float(0.0~1.0), "reasoning": "판정 근거 설명"}}"""
    
    def analyze_quality(self, row: dict) -> AIAnalysisResult:
        """
        개별 자재 품질 분석

        Args:
            row: 데이터 행 (딕셔너리)

        Returns:
            AIAnalysisResult: AI 분석 결과
        """
        time.sleep(self.settings.AI_REQUEST_DELAY)

        try:
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self.create_analysis_prompt(row)}
            ]

            response = self.qwen_client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.7,
                max_tokens=500,
            )
            response_text = response.choices[0].message.content
            
            # JSON 파싱 시도
            json_match = re.search(r'\{[^{}]*"suggested_grade"[^{}]*\}', response_text, re.DOTALL)
            
            if json_match:
                json_str = json_match.group()
                parsed = json.loads(json_str)
                return AIAnalysisResult(
                    suggested_grade=int(parsed.get('suggested_grade', 3)),
                    confidence=float(parsed.get('confidence', 0.5)),
                    reasoning=parsed.get('reasoning', 'AI 분석 완료')
                )
            else:
                # JSON을 찾지 못한 경우 기본값 반환
                return AIAnalysisResult(
                    suggested_grade=int(row.get('PART_Q_LEVEL', 3)),
                    confidence=0.3,
                    reasoning='AI 응답 파싱 실패, Rule Base 등급 유지'
                )
                
        except Exception as e:
            print(f"AI 분석 오류: {e}")
            return AIAnalysisResult(
                suggested_grade=int(row.get('PART_Q_LEVEL', 3)),
                confidence=0.3,
                reasoning=f'AI 분석 오류로 Rule Base 등급 유지: {str(e)[:50]}'
            )
    
    def batch_analyze_parallel(
        self,
        df: pd.DataFrame,
        max_workers: int = None,
        save_interval: int = None,
        output_path: str = None
    ) -> pd.DataFrame:
        """
        병렬 처리로 AI 분석 수행
        
        Args:
            df: 분석할 데이터프레임
            max_workers: 병렬 처리 스레드 수
            save_interval: 중간 저장 간격
            output_path: 중간 저장 파일 경로
        
        Returns:
            AI 분석 결과 DataFrame
        """
        max_workers = max_workers or self.settings.AI_MAX_WORKERS
        save_interval = save_interval or self.settings.AI_SAVE_INTERVAL
        
        # DataFrame 인덱스 리셋
        df = df.reset_index(drop=True)
        
        results = [None] * len(df)
        completed = 0
        start_time = time.time()
        
        print(f"병렬 AI 분석 시작 (스레드 수: {max_workers}, 총 {len(df)}건)")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 모든 분석 작업 제출
            future_to_idx = {
                executor.submit(self.analyze_quality, row.to_dict()): idx
                for idx, row in df.iterrows()
            }
            
            # 완료된 작업부터 결과 수집
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    result = future.result()
                    results[idx] = {
                        'suggested_grade': result.suggested_grade,
                        'confidence': result.confidence,
                        'reasoning': result.reasoning
                    }
                except Exception as e:
                    results[idx] = {
                        'suggested_grade': int(df.loc[idx, 'PART_Q_LEVEL']),
                        'confidence': 0.3,
                        'reasoning': f'분석 오류: {str(e)[:30]}'
                    }
                
                completed += 1
                
                # 진행 상황 출력
                if completed % 100 == 0 or completed == len(df):
                    elapsed = time.time() - start_time
                    if completed > 0:
                        avg_time = elapsed / completed
                        remaining_count = len(df) - completed
                        remaining_time = remaining_count * avg_time
                        
                        print(f"진행: {completed}/{len(df)} ({completed/len(df)*100:.1f}%) | "
                              f"경과: {elapsed/60:.1f}분 | "
                              f"예상 남은 시간: {remaining_time/60:.1f}분")
                
                # 중간 저장
                if save_interval > 0 and completed % save_interval == 0 and output_path:
                    self._save_intermediate(results, output_path)
        
        # 결과를 DataFrame으로 변환
        ai_df = pd.DataFrame(results)
        
        return ai_df
    
    def _save_intermediate(self, results: list, output_path: str):
        """중간 결과 저장"""
        try:
            temp_results = [r for r in results if r is not None]
            if temp_results:
                temp_df = pd.DataFrame(temp_results)
                temp_df.to_parquet(output_path)
        except Exception as e:
            print(f"중간 저장 실패: {e}")
    
    def batch_analyze(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        전체 자재 일괄 분석 (병렬 처리 사용)
        
        Args:
            df: 분석할 데이터프레임
        
        Returns:
            AI 분석 결과 DataFrame
        """
        return self.batch_analyze_parallel(df)
    
    def calculate_final_grade(self, rule_grade: int, ai_grade: int, confidence: float) -> Tuple[int, str]:
        """Calculate using this analyzer's settings, without creating a client."""
        return calculate_final_grade(rule_grade, ai_grade, confidence, settings=self.settings)


