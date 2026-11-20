"""
품질등급 Rule 기반 분석 엔진
- 입고검사 DMR 시스템 기반 품질등급 판정
- 기구/GLASS/APS: 7,000 PPM 기준
- 그 외 부품: 1,000 PPM 기준
"""

import pandas as pd
from dataclasses import dataclass

from I_ACE.config.settings import get_settings


@dataclass
class GradeResult:
    """등급 판정 결과"""
    level: int
    reason: str
    has_inspection: bool = True


class QualityRuleEngine:
    """
    품질등급 Rule 기반 분석기
    - 입고검사 품질수준을 1~5등급으로 분류
    - DMR(검사관리규정) 기준 적용
    """
    
    def __init__(self, settings=None):
        """
        초기화
        
        Args:
            settings: 설정 객체 (None이면 기본 설정 사용)
        """
        self.settings = settings or get_settings()
        
        # 카테고리별 PPM 임계값 (설정에서 로드)
        self.category_ppm_threshold = self.settings.CATEGORY_PPM_THRESHOLD
        self.default_ppm_threshold = self.settings.DEFAULT_PPM_THRESHOLD
    
    def _get_ppm_threshold(self, category: str) -> int:
        """
        카테고리별 PPM 임계값 반환
        
        Args:
            category: 카테고리명
        
        Returns:
            PPM 임계값
        """
        return self.category_ppm_threshold.get(category, self.default_ppm_threshold)
    
    @staticmethod
    def _to_float(row: pd.Series, col: str, default: float = 0.0) -> float:
        """숫자 컬럼을 안전하게 float로 변환한다."""
        value = row.get(col)
        return float(value) if pd.notna(value) else default
    
    def grade_one_ineligibility_reason(self, row: pd.Series) -> str | None:
        """신규 1등급에 필요한 최근 검사 근거를 확인한다."""
        ab_cnt = pd.to_numeric(row.get('AB_CNT'), errors='coerce')
        if pd.isna(ab_cnt) or ab_cnt != 0:
            return '협력사 이상감지 0건 확인 필요'
        recent_qty = 0.0
        for period in ('MONTH', 'WEEK'):
            qty = pd.to_numeric(row.get(f'{period}_INSP_QTY'), errors='coerce')
            defects = pd.to_numeric(row.get(f'{period}_INSP_DEF_QTY'), errors='coerce')
            ppm = pd.to_numeric(row.get(f'{period}_INSP_PPM'), errors='coerce')
            if pd.isna(qty) or qty < 0 or pd.isna(defects) or defects != 0:
                return '월간·주간 검사수와 무불량 확인 필요'
            if pd.isna(ppm) or ppm != 0:
                return '월간·주간 입고검사 PPM 0 확인 필요'
            recent_qty += qty
        minimum = getattr(self.settings, 'GRADE_ONE_MIN_INSP_QTY', 100)
        if recent_qty < minimum:
            return f'최근 검사수 {recent_qty:.0f}/{minimum}개로 1등급 확인 보류'
        return None

    def _finalize_with_ab(self, row: pd.Series, level: int, reason: str) -> GradeResult:
        """
        AB_CNT를 최종 단계에서만 반영한다.
        - AB_CNT 결측: 협력사 데이터 미제공으로 1등급 불가
        - AB_CNT = 0: 사전 2등급 + 최근 무불량·검사수 조건 충족 시 1등급
        - AB_CNT >= 1: 사전 등급에서 위험도를 1단계 상향 [AB악화] 태그 추가
        """
        base_level = max(2, min(5, int(level)))
        raw_ab_cnt = row.get('AB_CNT')

        if pd.isna(raw_ab_cnt):
            return GradeResult(
                level=base_level,
                reason=f'{reason}; [AB] 협력사 데이터 미제공으로 1등급 제외'
            )

        ab_cnt = float(raw_ab_cnt)
        if ab_cnt <= 0:
            if base_level == 2:
                ineligible_reason = self.grade_one_ineligibility_reason(row)
                if ineligible_reason:
                    return GradeResult(level=2, reason=f'{reason}; [1등급보류] {ineligible_reason}')
                return GradeResult(
                    level=1,
                    reason=f'{reason}; [AB] 이상감지 0건으로 최우수 판정'
                )
            return GradeResult(
                level=base_level,
                reason=f'{reason}; [AB] 이상감지 0건, 등급 유지'
            )

        adjusted_level = min(5, base_level + 1)
        return GradeResult(
            level=adjusted_level,
            reason=f'{reason}; [AB악화] 이상감지 {ab_cnt:.0f}건으로 위험도 1단계 상향'
        )
    
    def calculate_base_level(self, row: pd.Series, prefix: str) -> GradeResult:
        """
        기초 레벨 계산 (TOTAL_LEVEL, MONTH_LEVEL, WEEK_LEVEL)
        입고검사 PPM 기반 등급 판정
        
        Args:
            row: 데이터 행
            prefix: 'TOTAL', 'MONTH', 'WEEK'
        
        Returns:
            GradeResult: 등급 판정 결과
        """
        # 컬럼명
        ppm_col = f'{prefix}_INSP_PPM'
        fail_rate_col = f'{prefix}_FAIL_RATE'
        insp_qty_col = f'{prefix}_INSP_QTY'
        def_qty_col = f'{prefix}_INSP_DEF_QTY'

        # 값 추출 (숫자 타입 변환)
        ppm = float(row[ppm_col]) if pd.notna(row.get(ppm_col)) else 0
        fail_rate = float(row[fail_rate_col]) if pd.notna(row.get(fail_rate_col)) else 0
        insp_qty = float(row[insp_qty_col]) if pd.notna(row.get(insp_qty_col)) else 0
        def_qty = float(row[def_qty_col]) if pd.notna(row.get(def_qty_col)) else 0
        category = row['CATEGORY_L'] if pd.notna(row.get('CATEGORY_L')) else ''

        # 카테고리별 임계값
        threshold = self._get_ppm_threshold(category)

        # 표본 가드 설정
        min_insp_qty = getattr(self.settings, 'MIN_INSP_QTY_FOR_LEVEL', 5)
        min_def_qty = getattr(self.settings, 'MIN_DEF_QTY_FOR_PPM_ALERT', 2)

        # 검사 이력 확인 (극소 표본은 이력 없음 취급)
        has_insp = insp_qty >= min_insp_qty

        # 검사 이력이 없으면 기본 3등급
        if not has_insp:
            if insp_qty > 0:
                return GradeResult(
                    level=3,
                    reason=f'검사수 {insp_qty:.0f}건으로 판정 보류 (최소 {min_insp_qty}건)',
                    has_inspection=False
                )
            return GradeResult(level=3, reason='검사이력 없음', has_inspection=False)

        # 등급 판정 (입고검사 PPM 기준 + 최소 불량 건수 가드)
        if ppm >= threshold and def_qty >= min_def_qty and fail_rate >= 50:
            base_level = 5  # 품질 경고 기준 초과 + LOT 불합격률 높음
        elif ppm >= threshold and def_qty >= min_def_qty:
            base_level = 4  # 품질 경고 기준 초과
        elif ppm >= threshold:
            # PPM은 초과했으나 불량 건수 미달 (소표본 단발 불량) → 주의까지만
            return GradeResult(
                level=3,
                reason=f'PPM {ppm:.0f} 초과이나 불량 {def_qty:.0f}건으로 경고 보류',
                has_inspection=True
            )
        elif ppm >= threshold * 0.5:  # 임계값의 50%
            base_level = 3  # 주의 구간
        elif ppm >= 1:
            base_level = 2  # 양호
        else:
            base_level = 1  # 최우수 (불량 없음)

        return GradeResult(level=base_level, reason=f'PPM {ppm:.0f}', has_inspection=True)
    
    def determine_final_level(self, row: pd.Series) -> GradeResult:
        """
        최종 등급 판정
        다양한 품질 지표를 종합하여 최종 등급 결정
        
        Args:
            row: 데이터 행
        
        Returns:
            GradeResult: 최종 등급 판정 결과
        """
        t_lv = int(row.get('TOTAL_LEVEL', 3))
        m_lv = int(row.get('MONTH_LEVEL', 3))
        w_lv = int(row.get('WEEK_LEVEL', 3))
        category = row['CATEGORY_L'] if pd.notna(row.get('CATEGORY_L')) else ''
        threshold = self._get_ppm_threshold(category)
        min_recovery_qty = getattr(self.settings, 'MIN_RECOVERY_INSP_QTY', 100)
        
        # 검사 이력 여부
        m_has_insp = bool(row.get('MONTH_HAS_INSP', True))
        w_has_insp = bool(row.get('WEEK_HAS_INSP', True))
        
        # ============================================
        # 품질 지표 추출
        # ============================================
        
        # 입고검사 관련 (숫자 타입 변환)
        t_insp_ppm = self._to_float(row, 'TOTAL_INSP_PPM')
        m_insp_ppm = self._to_float(row, 'MONTH_INSP_PPM')
        w_insp_ppm = self._to_float(row, 'WEEK_INSP_PPM')
        m_def_qty = self._to_float(row, 'MONTH_INSP_DEF_QTY')
        w_def_qty = self._to_float(row, 'WEEK_INSP_DEF_QTY')
        w_process_def_qty = self._to_float(row, 'defect_qty_WEEK')
        m_insp_qty = self._to_float(row, 'MONTH_INSP_QTY')
        w_insp_qty = self._to_float(row, 'WEEK_INSP_QTY')
        
        # 불량률 이탈 (MQIS 트렌드)
        leave_cnt = self._to_float(row, 'LEAVE_CNT')
        
        # 우리공정 불량률 추세
        trend = self._to_float(row, 'ppm_trend_ratio')
        
        # ============================================
        # 1단계: 긴급 상황 (Emergency) - 5등급
        # ============================================

        # 1-1. 우리공정 불량률 3배 이상 폭증 + 주간 공정 불량 발생 [공정악화]
        if trend >= self.settings.PPM_TREND_HIGH and w_process_def_qty >= self.settings.DEFECT_QTY_EMERGENCY:
            return self._finalize_with_ab(
                row,
                5,
                f'[긴급] [공정악화] 공정불량률 {trend:.1f}배 폭증 + 공정 주간불량 {w_process_def_qty:.0f}건'
            )

        # 1-2. 주간 입고검사 PPM이 경고기준 2배 이상 + 입고불량 10건 이상 [IQC악화]
        if (
            w_has_insp and
            w_insp_ppm >= threshold * 2 and
            w_def_qty >= self.settings.INSP_DEF_QTY_EMERGENCY
        ):
            return self._finalize_with_ab(
                row,
                5,
                (
                    f'[긴급] [IQC악화] WEEK 입고검사 PPM {w_insp_ppm:.0f} '
                    f'(경고기준 {threshold}의 2배 이상) + 입고불량 {w_def_qty:.0f}건'
                )
            )

        # 1-3. 월간 입고검사 PPM이 경고기준 2배 이상 + 입고불량 10건 이상 [IQC악화]
        if m_has_insp and m_insp_ppm >= threshold * 2 and m_def_qty >= self.settings.INSP_DEF_QTY_EMERGENCY:
            return self._finalize_with_ab(
                row,
                5,
                (
                    f'[긴급] [IQC악화] MONTH 입고검사 PPM {m_insp_ppm:.0f} '
                    f'(경고기준 {threshold}의 2배 이상) + 입고불량 {m_def_qty:.0f}건'
                )
            )
        
        # ============================================
        # 2단계: 주의 상황 (Warning) - 등급 상향
        # ============================================

        reasons = []
        penalty = 0
        has_process_penalty = False  # 공정불량률 패널티 여부
        has_iqc_penalty = False       # 입고검사 패널티 여부

        # 2-1. 우리공정 불량률 1.5배 이상 상승 [공정악화]
        if trend >= self.settings.PPM_TREND_WARNING:
            penalty += 1
            has_process_penalty = True
            reasons.append(f'[공정악화] 공정불량률 {trend:.1f}배 상승')

        # 2-2. 불량률 이탈 발생 (MQIS 트렌드)
        if leave_cnt > 0:
            penalty += 1
            reasons.append(f'불량률 이탈 {leave_cnt:.0f}건')

        # PPM 경고 판정 최소 불량 건수 (소표본 단발 불량 노이즈 방지)
        min_def_qty = getattr(self.settings, 'MIN_DEF_QTY_FOR_PPM_ALERT', 2)

        # 2-3. 주간 입고검사 PPM이 경고기준 초과 (불량 건수 병행 조건) [IQC악화]
        if w_has_insp and w_insp_ppm >= threshold and w_def_qty >= min_def_qty:
            penalty += 1
            has_iqc_penalty = True
            reasons.append(f'[IQC악화] WEEK 입고검사 PPM {w_insp_ppm:.0f} 초과 (불량 {w_def_qty:.0f}건)')

        # 2-4. 월간 입고검사 PPM이 경고기준 초과 (불량 건수 병행 조건) [IQC악화]
        if m_has_insp and m_insp_ppm >= threshold and m_def_qty >= min_def_qty:
            penalty += 1
            has_iqc_penalty = True
            reasons.append(f'[IQC악화] MONTH 입고검사 PPM {m_insp_ppm:.0f} 초과 (불량 {m_def_qty:.0f}건)')
        
        # 2-5. MONTH와 WEEK 모두 검사 이력 없음
        no_insp_at_all = (not m_has_insp) and (not w_has_insp)
        if no_insp_at_all:
            reasons.append('MONTH/WEEK 검사이력 없음')
        
        # ============================================
        # 3단계: 최종 등급 계산
        # ============================================
        
        # 기본 등급: TOTAL 등급 기준. AB 고려 전에는 1등급을 만들지 않는다.
        base_level = max(2, t_lv)
        
        # 주의 요인이 있으면 등급 상향
        if penalty > 0:
            # 페널티에 따른 등급 상향 (최대 4등급까지만 기본 상향)
            adjusted_level = min(4, base_level + penalty)
            
            # 단기 등급이 더 나쁘면 그것을 반영 (검사 이력 있는 경우만)
            if m_has_insp and m_lv >= 4:
                adjusted_level = max(adjusted_level, m_lv)
            if w_has_insp and w_lv >= 4:
                adjusted_level = max(adjusted_level, w_lv)
            
            reason_str = '[주의] ' + ', '.join(reasons)
            return self._finalize_with_ab(row, adjusted_level, reason_str)
        
        # ============================================
        # 4단계: 검사 이력 없는 경우 처리
        # ============================================
        
        # MONTH와 WEEK 모두 검사 이력 없으면 최소 3등급
        if no_insp_at_all:
            return self._finalize_with_ab(
                row,
                max(3, base_level),
                '[유지] MONTH/WEEK 검사이력 없음, 기존등급 유지'
            )
        
        # MONTH 검사 이력만 있으면 MONTH 등급 기준
        if m_has_insp and not w_has_insp:
            # TOTAL 이력이 경고/주의인데 WEEK 확인 데이터가 없으면,
            # 충분한 MONTH 검사수에서만 1단계 개선한다.
            if base_level >= 3 and m_lv <= 2:
                if m_insp_qty >= min_recovery_qty:
                    improved_level = max(2, base_level - 1)
                    return self._finalize_with_ab(
                        row,
                        improved_level,
                        (
                            f'[개선] MONTH 검사 PPM {m_insp_ppm:.0f}, WEEK 이력 없음, '
                            f'TOTAL PPM {t_insp_ppm:.0f} 반영'
                        )
                    )
                return self._finalize_with_ab(
                    row,
                    base_level,
                    (
                        f'[유지] MONTH 검사수 {m_insp_qty:.0f}건으로 개선 판단 보류, '
                        f'TOTAL PPM {t_insp_ppm:.0f} 유지'
                    )
                )

            # MONTH 품질이 양호하면 MONTH 등급 사용. AB 고려 전에는 최소 2등급.
            if m_lv <= 2:
                return self._finalize_with_ab(
                    row,
                    2,
                    f'[정상] MONTH 검사 PPM {m_insp_ppm:.0f}, WEEK 이력 없음'
                )
            return self._finalize_with_ab(
                row,
                max(2, m_lv),
                f'[정상] MONTH 검사 PPM {m_insp_ppm:.0f}, WEEK 이력 없음'
            )
        
        # ============================================
        # 5단계: 품질 개선 확인 (Recovery)
        # ============================================
        
        # 장기 등급은 낮지만 최근 품질이 우수하면 등급 하향
        if base_level >= 3 and w_lv <= 2 and m_lv <= 2:
            enough_recent_insp = (
                m_has_insp and w_has_insp and
                m_insp_qty >= min_recovery_qty and w_insp_qty >= min_recovery_qty
            )
            if enough_recent_insp and w_insp_ppm < threshold * 0.1:
                improved_level = max(2, base_level - 1)
                return self._finalize_with_ab(
                    row,
                    improved_level,
                    f'[개선] 최근 품질우수 (WEEK PPM {w_insp_ppm:.0f})'
                )
        
        # ============================================
        # 6단계: 최종 등급 확정
        # ============================================
        
        return self._finalize_with_ab(
            row,
            base_level,
            f'[정상] 입고검사 PPM {w_insp_ppm:.0f}, TOTAL 등급 유지'
        )
    
    def run_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        전체 분석 실행
        
        Args:
            df: 분석할 데이터프레임
        
        Returns:
            분석 결과 데이터프레임
        """
        print('='*60)
        print('품질등급 분석 (Rule Engine) 시작')
        print('='*60)
        print('\n📋 품질 경고 기준:')
        print('   - 기구/GLASS/APS: 7,000 PPM')
        print('   - 그 외 부품: 1,000 PPM')
        
        df = df.copy()
        
        # 1. 기초 레벨 계산
        print('\n[1단계] 기초 레벨 계산 (TOTAL, MONTH, WEEK)')
        
        total_results = df.apply(lambda r: self.calculate_base_level(r, 'TOTAL'), axis=1)
        month_results = df.apply(lambda r: self.calculate_base_level(r, 'MONTH'), axis=1)
        week_results = df.apply(lambda r: self.calculate_base_level(r, 'WEEK'), axis=1)
        
        df['TOTAL_LEVEL'] = [r.level for r in total_results]
        df['MONTH_LEVEL'] = [r.level for r in month_results]
        df['WEEK_LEVEL'] = [r.level for r in week_results]
        
        # 검사 이력 여부 저장
        df['TOTAL_HAS_INSP'] = [r.has_inspection for r in total_results]
        df['MONTH_HAS_INSP'] = [r.has_inspection for r in month_results]
        df['WEEK_HAS_INSP'] = [r.has_inspection for r in week_results]
        
        # 2. 최종 등급 판정
        print('[2단계] 최종 등급 판정')
        results = df.apply(self.determine_final_level, axis=1)
        df['PART_Q_LEVEL'] = [r.level for r in results]
        df['LEVEL_REASON'] = [r.reason for r in results]
        
        # 3. 중복 제거
        df = df.drop_duplicates()
        
        # 4. 결과 출력
        self._print_results(df)
        
        return df
    
    def _print_results(self, df: pd.DataFrame):
        """결과 통계 출력"""
        print('\n' + '='*60)
        print('등급 분포 결과')
        print('='*60)
        
        print('\n[기초 레벨 분포]')
        for prefix in ['TOTAL', 'MONTH', 'WEEK']:
            col = f'{prefix}_LEVEL'
            print(f'\n{prefix}_LEVEL:')
            print(df[col].value_counts().sort_index())
        
        print('\n[최종 등급 분포]')
        print(df['PART_Q_LEVEL'].value_counts().sort_index())
        
        print('\n[CATEGORY_L별 최종 등급 분포]')
        for cat in df['CATEGORY_L'].unique():
            if pd.isna(cat):
                continue
            subset = df[df['CATEGORY_L'] == cat]
            threshold = self._get_ppm_threshold(cat)
            print(f'\n[{cat}] ({len(subset)}건, 경고기준: {threshold} PPM)')
            level_dist = subset['PART_Q_LEVEL'].value_counts().sort_index()
            for level in [1, 2, 3, 4, 5]:
                cnt = level_dist.get(level, 0)
                pct = cnt / len(subset) * 100
                print(f'  {level}등급: {cnt}건 ({pct:.1f}%)')
        
        print('\n[등급별 주요 사유]')
        for level in [1, 2, 3, 4, 5]:
            subset = df[df['PART_Q_LEVEL'] == level]
            print(f'\n{level}등급 ({len(subset)}건):')
            if len(subset) > 0:
                top_reasons = subset['LEVEL_REASON'].value_counts().head(5)
                for reason, cnt in top_reasons.items():
                    print(f'  - {reason}: {cnt}건')