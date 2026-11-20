"""
스케줄러 모듈
- 매일 오전 6시 분석 실행
- APScheduler 사용
"""

import os

# 텔레메트리 및 통계 수집 비활성화
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['DISABLE_TELEMETRY'] = '1'
os.environ['PLAUSIBLE_DISABLE'] = '1'

# 프록시 관련 환경 변수 정리 (Selenium Manager 오류 방지)
# 반드시 다른 import보다 먼저 실행되어야 함
proxy_vars = ['http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ftp_proxy', 'FTP_PROXY']
for var in proxy_vars:
    if var in os.environ:
        value = os.environ[var]
        if value and not value.startswith(('http://', 'https://', 'direct://')):
            os.environ[var] = f'http://{value}'

import sys
from pathlib import Path
from datetime import datetime

# 경로 설정
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from I_ACE.config.settings import get_settings
from I_ACE.data.collector import DataCollector
from I_ACE.data.preprocessor import DataPreprocessor
from data.plant_defect_collector import save_plant_defect_data
from I_ACE.engine.rule_engine import QualityRuleEngine
from I_ACE.engine.ml_analyzer import QualityMLAnalyzer
from I_ACE.engine.grade_calculator import calculate_final_grade
from I_ACE.persistence.crud import save_analysis_result, save_analysis_result_to_databases, get_recent_grade_history
from database.db import init_db
from database.db import SessionLocal

import pandas as pd


# 스케줄러 인스턴스
scheduler = BackgroundScheduler()


def _get_grade_exclusion_reason(row: pd.Series) -> str | None:
    """등급 판정 제외 대상이면 reason을 반환한다."""
    category_l = row.get('CATEGORY_L')
    category_s = row.get('CATEGORY_S')

    category_l_text = '' if pd.isna(category_l) else str(category_l).strip()
    category_s_text = '' if pd.isna(category_s) else str(category_s).strip()

    if category_l_text == '반도체':
        return '반도체부품등급 check5로 등급 판정 제외'
    if 'battery' in category_s_text.lower():
        return 'Battery 부품등급 판정 제외'
    if 'skd(mx)' in category_s_text.lower():
        return 'SKD 부품등급 판정 제외'
    if '별매품(HAWA)' in category_s_text.upper():
        return '별매품(HAWA) 부품등급 판정 제외'

    return None


def _is_no_insp_state(row: pd.Series) -> bool:
    """
    검사감소(무검사) 상태 판별
    - 입고는 있는데 주간 검사 실적이 없는 상태 (자율제어에 의한 검사 skip)
    - 입고 자체가 없으면(거래중단) False
    """
    week_insp = float(row.get('WEEK_INSP_QTY') or 0)
    if week_insp > 0:
        return False

    incoming = (
        float(row.get('WEEK_GR_QTY') or 0) > 0
        or float(row.get('WEEK_LOT_CNT') or 0) > 0
        or float(row.get('MONTH_GR_QTY') or 0) > 0
        or float(row.get('MONTH_LOT_CNT') or 0) > 0
    )
    return incoming


def _is_clean_alt_signals(row: pd.Series, settings) -> bool:
    """
    무검사 기간 대체 감시 신호 청정 여부
    - AB_CNT: 협력사 측정데이터 이상감지 (결측=미제공이면 혜택 불가)
    - LEAVE_CNT: MQIS 트렌드 이탈
    - defect_qty_WEEK: 우리공정 주간 불량
    - ppm_trend_ratio: 공정불량률 추세
    """
    ab_cnt = row.get('AB_CNT')
    if pd.isna(ab_cnt) or float(ab_cnt) > 0:
        return False

    leave_cnt = float(row.get('LEAVE_CNT') or 0)
    week_defect = float(row.get('defect_qty_WEEK') or 0)
    trend = float(row.get('ppm_trend_ratio') or 0)

    return (
        leave_cnt == 0
        and week_defect == 0
        and trend < getattr(settings, 'PPM_TREND_WARNING', 1.5)
    )


def _is_no_insp_hold_eligible(row: pd.Series, settings) -> bool:
    """Hold only a low-risk grade affected by missing inspection history."""
    if not _is_no_insp_state(row) or not _is_clean_alt_signals(row, settings):
        return False

    # PART_Q_LEVEL retains ML escalation even if AI replaces LEVEL_REASON.
    # Missing history alone can propose grade 3, but never a warning grade 4/5.
    for column in ('PART_Q_LEVEL', 'FINAL_GRADE'):
        grade = pd.to_numeric(row.get(column), errors='coerce')
        if pd.isna(grade) or not 1 <= grade <= 3:
            return False

    for period in ('MONTH', 'WEEK'):
        defects = pd.to_numeric(row.get(f'{period}_INSP_DEF_QTY'), errors='coerce')
        ppm = pd.to_numeric(row.get(f'{period}_INSP_PPM'), errors='coerce')
        if (pd.notna(defects) and defects > 0) or (pd.notna(ppm) and ppm > 0):
            return False

    # Reuse Rule thresholds/sample guards, rather than relying on reason text.
    # An observed caution/warning is not a missing-history-only proposal.
    engine = QualityRuleEngine(settings)
    for period in ('TOTAL', 'MONTH', 'WEEK'):
        result = engine.calculate_base_level(row, period)
        if result.has_inspection and result.level >= 3:
            return False
    return True


def apply_grade_ratchet(rule_result: pd.DataFrame, db, settings) -> pd.DataFrame:
    """
    등급 래칫(단계 하향) 적용
    - 악화/동일: 하이브리드 등급 즉시 반영
    - 개선: 연속 GRADE_RECOVERY_CONSECUTIVE회 개선신호
      + 검사수(MONTH+WEEK) >= GRADE_RECOVERY_MIN_INSP_QTY 충족 시
      이전 최종등급에서 1단계만 하향, 미충족 시 이전 등급 유지
    - 래칫 적용 전 등급은 HYBRID_GRADE 컬럼에 보존 (개선신호 연속성 판단 근거)
    """
    rule_result = rule_result.copy()
    # AI 제안도 신규 1등급 근거를 우회할 수 없다. 기존 1등급 무검사 유지는 이후 별도 판단.
    grade_engine = QualityRuleEngine(settings)
    for idx, row in rule_result.iterrows():
        if row['FINAL_GRADE'] != 1 or _get_grade_exclusion_reason(row):
            continue
        rule_grade = pd.to_numeric(row.get('PART_Q_LEVEL'), errors='coerce')
        reason = grade_engine.grade_one_ineligibility_reason(row)
        if pd.isna(rule_grade) or rule_grade not in (1, 2):
            reason = 'Rule/ML 위험 해소 확인 필요'
        if reason:
            rule_result.at[idx, 'FINAL_GRADE'] = 2
            for column in ('FINAL_REASON', 'LEVEL_REASON'):
                rule_result.at[idx, column] = f"{row[column]}; [1등급보류] {reason}"
    # 1등급 자격 확인 후 후보를 회복 이력으로 보존한다.
    rule_result['HYBRID_GRADE'] = rule_result['FINAL_GRADE']

    # 1회성 래칫 우회: 만료일(GRADE_RATCHET_BYPASS_UNTIL) 이하인 실행일에는
    # 하이브리드 등급을 그대로 반영한다. 제외 대상(반도체/Battery/SKD)은 이미 3등급 고정.
    bypass_until = pd.to_datetime(getattr(settings, 'GRADE_RATCHET_BYPASS_UNTIL', '') or None, errors='coerce')
    if pd.notna(bypass_until) and pd.Timestamp.now().normalize() <= bypass_until.normalize():
        bypass_reason = (
            f"[일괄반영] 집계 기준 변경(협력사 단위)으로 하이브리드 등급 즉시 반영 "
            f"(래칫 우회, 만료 {bypass_until:%Y-%m-%d})"
        )
        for idx, row in rule_result.iterrows():
            if _get_grade_exclusion_reason(row):
                continue
            rule_result.at[idx, 'FINAL_REASON'] = f"{row['FINAL_REASON']}; {bypass_reason}"
            rule_result.at[idx, 'LEVEL_REASON'] = f"{row['LEVEL_REASON']}; {bypass_reason}"
        print(f"  래칫 우회 적용 (만료 {bypass_until:%Y-%m-%d}) - 하이브리드 등급 그대로 사용")
        return rule_result

    if not getattr(settings, 'GRADE_RATCHET_ENABLED', True):
        print("  래칫 비활성화 상태 - 하이브리드 등급 그대로 사용")
        return rule_result

    lookback = getattr(settings, 'GRADE_HISTORY_LOOKBACK_UPLOADS', 10)
    consecutive_required = getattr(settings, 'GRADE_RECOVERY_CONSECUTIVE', 4)
    min_insp_qty = getattr(settings, 'GRADE_RECOVERY_MIN_INSP_QTY', 100)
    max_hold = getattr(settings, 'GRADE_RECOVERY_MAX_HOLD', 15)

    # 회복유지 한도 카운트에 필요한 만큼 이력 조회 범위 확장
    lookback = max(lookback, max_hold + 2)

    # 무검사유지 설정
    no_insp_enabled = getattr(settings, 'NO_INSP_HOLD_ENABLED', True)
    no_insp_max_grade = getattr(settings, 'NO_INSP_HOLD_MAX_GRADE', 2)
    no_insp_max_consecutive = getattr(settings, 'NO_INSP_HOLD_MAX_CONSECUTIVE', 70)

    # 무검사유지 연속 카운트에 필요한 만큼 이력 조회 범위 확장
    if no_insp_enabled:
        lookback = max(lookback, no_insp_max_consecutive + 2)

    try:
        history = get_recent_grade_history(db, lookback)
    except Exception as e:
        raise RuntimeError('등급 이력 조회 실패로 회복 조건을 검증할 수 없습니다.') from e

    if history.empty:
        print("  등급 이력 없음 - 래칫 미적용 (최초 실행)")
        return rule_result

    # (part_cd, vend_l_cd)별 이력 그룹 (upload 내림차순 유지)
    grouped = {
        key: grp for key, grp in history.groupby(['part_cd', 'vend_l_cd'], dropna=False)
    }

    stat_worse = 0
    stat_equal = 0
    stat_hold = 0
    stat_down = 0
    stat_expire = 0
    stat_new = 0
    stat_noinsp_hold = 0
    stat_noinsp_expire = 0

    for idx, row in rule_result.iterrows():
        # 등급 판정 제외 대상(반도체/Battery 등)은 래칫 미적용
        if _get_grade_exclusion_reason(row):
            continue

        hybrid = int(row['FINAL_GRADE'])
        key = (row.get('PART_CD'), row.get('VEND_L_CD'))
        grp = grouped.get(key)

        # 이력 없는 신규 부품: 오늘 하이브리드 그대로
        if grp is None or grp.empty or pd.isna(grp.iloc[0]['final_grade']):
            stat_new += 1
            continue

        prev = int(grp.iloc[0]['final_grade'])

        # 악화 또는 동일
        if hybrid > prev:
            # 무검사유지 판정: 상향 원인이 실제 악화가 아닌 데이터 부재인 경우
            # 조건: 우수 등급(prev<=상한) + 검사감소 상태(입고O/검사X) + 대체 신호 청정
            if (
                hybrid > prev
                and no_insp_enabled
                and prev <= no_insp_max_grade
                and _is_no_insp_hold_eligible(row, settings)
            ):
                # 연속 무검사유지 횟수: 당일 포함 1 + 과거 hybrid>final(유지했던 날) 연속
                hold_streak = 1
                for _, hist_row in grp.iterrows():
                    h_hybrid = hist_row['hybrid_grade']
                    h_final = hist_row['final_grade']
                    if (
                        pd.notna(h_hybrid) and pd.notna(h_final)
                        and int(h_hybrid) > int(h_final)
                    ):
                        hold_streak += 1
                    else:
                        break

                if hold_streak <= no_insp_max_consecutive:
                    ten_week_yn = str(row.get('TEN_WEEK_INSP_YN') or '').strip().upper()
                    ten_week_note = ', 10주 확인검사 대상' if ten_week_yn == 'Y' else ''
                    ratchet_reason = (
                        f'[무검사유지] 입고 있음/검사 없음, 대체신호 청정'
                        f'(AB 0건, 이탈 0건, 공정불량 0건), '
                        f'유지 {hold_streak}/{no_insp_max_consecutive}회{ten_week_note}, '
                        f'등급 {prev} 유지 (하이브리드 제안 {hybrid})'
                    )
                    rule_result.at[idx, 'FINAL_GRADE'] = prev
                    rule_result.at[idx, 'FINAL_REASON'] = f"{row['FINAL_REASON']}; {ratchet_reason}"
                    rule_result.at[idx, 'LEVEL_REASON'] = f"{row['LEVEL_REASON']}; {ratchet_reason}"
                    stat_noinsp_hold += 1
                    continue

                # 한도 초과: 확인검사 없이는 더 이상 유지 불가 → 상향 반영
                ratchet_reason = (
                    f'[무검사한도초과] 무검사유지 {hold_streak}회로 한도'
                    f'({no_insp_max_consecutive}회) 초과, 확인검사 필요, '
                    f'하이브리드 등급 {hybrid} 반영'
                )
                rule_result.at[idx, 'FINAL_REASON'] = f"{row['FINAL_REASON']}; {ratchet_reason}"
                rule_result.at[idx, 'LEVEL_REASON'] = f"{row['LEVEL_REASON']}; {ratchet_reason}"
                stat_noinsp_expire += 1
                continue

            # 실제 악화: 즉시 반영
            # [수정] 동일(hybrid==prev)은 래칫 미적용, 이전 등급 유지
            stat_worse += 1
            continue

        # 동일한 경우
        if hybrid == prev:
            # 무검사 상태인지 확인: 무검사유지 혜택 가능한지 판단
            # 개선 신호가 아니므로 "회복유지" 카운트 진행 없음
            if (
                no_insp_enabled
                and prev <= no_insp_max_grade
                and _is_no_insp_hold_eligible(row, settings)
            ):
                hold_streak = 1
                for _, hist_row in grp.iterrows():
                    h_hybrid = hist_row['hybrid_grade']
                    h_final = hist_row['final_grade']
                    if (
                        pd.notna(h_hybrid) and pd.notna(h_final)
                        and int(h_hybrid) > int(h_final)
                    ):
                        hold_streak += 1
                    else:
                        break

                if hold_streak <= no_insp_max_consecutive:
                    ten_week_yn = str(row.get('TEN_WEEK_INSP_YN') or '').strip().upper()
                    ten_week_note = ', 10주 확인검사 대상' if ten_week_yn == 'Y' else ''
                    ratchet_reason = (
                        f'[무검사유지] 입고 있음/검사 없음, 대체신호 청정'
                        f'(AB 0건, 이탈 0건, 공정불량 0건), '
                        f'유지 {hold_streak}/{no_insp_max_consecutive}회{ten_week_note}, '
                        f'등급 {prev} 유지 (동일 등급)'
                    )
                    rule_result.at[idx, 'FINAL_REASON'] = f"{row['FINAL_REASON']}; {ratchet_reason}"
                    rule_result.at[idx, 'LEVEL_REASON'] = f"{row['LEVEL_REASON']}; {ratchet_reason}"
                    stat_noinsp_hold += 1
                    continue

                ratchet_reason = (
                    f'[무검사한도초과] 무검사유지 {hold_streak}회로 한도'
                    f'({no_insp_max_consecutive}회) 초과, 확인검사 필요'
                )
                rule_result.at[idx, 'FINAL_REASON'] = f"{row['FINAL_REASON']}; {ratchet_reason}"
                rule_result.at[idx, 'LEVEL_REASON'] = f"{row['LEVEL_REASON']}; {ratchet_reason}"
                stat_noinsp_expire += 1
            stat_equal += 1
            continue

        # 모든 개선 원인에 동일한 회복 조건을 적용한다. 회차 내 제조사 행 중복은 1회로 센다.
        consecutive = 1
        upload_groups = list(grp.groupby('upload', sort=False, dropna=False))
        for position, (upload, rows) in enumerate(upload_groups):
            if pd.isna(upload) or position + 1 >= len(upload_groups):
                break
            previous_rows = upload_groups[position + 1][1]
            # 등급을 낮춘 회차 자체는 다음 단계의 회복 대기로 세지 않는다.
            held_at_prev = (
                rows['final_grade'].notna().all()
                and rows['hybrid_grade'].notna().all()
                and (rows['final_grade'] == prev).all()
                and (rows['hybrid_grade'] < prev).all()
                and previous_rows['final_grade'].notna().all()
                and (previous_rows['final_grade'] == prev).all()
            )
            if not held_at_prev:
                break
            consecutive += 1

        month_qty = float(row.get('MONTH_INSP_QTY') or 0)
        week_qty = float(row.get('WEEK_INSP_QTY') or 0)
        insp_qty = month_qty + week_qty

        # 개선에는 실제 검사 근거가 필요하다. 대체 신호만으로 새 등급을 부여하지 않는다.
        qty_ok = insp_qty >= min_insp_qty
        qty_note = f'검사수 {insp_qty:.0f}개'

        # 회복유지 한도: 입고 자체가 없으면 검사수를 채울 수 없으므로
        # 개선신호가 한도 횟수에 도달하면 하이브리드 등급을 그대로 반영한다.
        no_incoming = all(
            float(row.get(col) or 0) == 0
            for col in ('WEEK_GR_QTY', 'WEEK_LOT_CNT', 'MONTH_GR_QTY', 'MONTH_LOT_CNT')
        )
        hold_expired = consecutive >= max_hold and no_incoming

        if consecutive >= consecutive_required and qty_ok:
            # 조건 충족: 1단계만 하향 (hybrid < prev 이므로 prev-1 >= hybrid 보장 불필요)
            new_grade = max(1, prev - 1)
            ratchet_reason = (
                f'[단계회복] 개선신호 {consecutive}회 + {qty_note}, '
                f'{prev}→{new_grade} (하이브리드 제안 {hybrid})'
            )
            stat_down += 1
        elif hold_expired:
            new_grade = hybrid
            ratchet_reason = (
                f'[회복한도] 개선신호 {consecutive}회로 한도({max_hold}회) 도달, '
                f'입고 없음, 하이브리드 등급 반영 {prev}→{new_grade}'
            )
            stat_expire += 1
        else:
            # 조건 미충족: 이전 등급 유지
            new_grade = prev
            ratchet_reason = (
                f'[회복유지] 개선신호 {consecutive}/{consecutive_required}회, '
                f'검사수 {insp_qty:.0f}/{min_insp_qty}건, '
                f'등급 {prev} 유지 (하이브리드 제안 {hybrid})'
            )
            stat_hold += 1

        rule_result.at[idx, 'FINAL_GRADE'] = new_grade
        rule_result.at[idx, 'FINAL_REASON'] = f"{row['FINAL_REASON']}; {ratchet_reason}"
        rule_result.at[idx, 'LEVEL_REASON'] = f"{row['LEVEL_REASON']}; {ratchet_reason}"

    print(
        f"  래칫 결과 - 악화(즉시반영): {stat_worse}건, 동일(유지): {stat_equal}건, "
        f"단계하향: {stat_down}건, 회복유지: {stat_hold}건, 회복한도: {stat_expire}건, 신규: {stat_new}건, "
        f"무검사유지: {stat_noinsp_hold}건, 무검사한도초과: {stat_noinsp_expire}건"
    )

    return rule_result


def run_scheduled_analysis(iqc_file_path: str = None):
    """
    스케줄된 분석 실행
    
    Args:
        iqc_file_path: 입고실적 데이터 파일 경로
    """

    # [마지막 수단] 현재 스크립트(scheduler.py) 위치를 기준으로 프로젝트 루트 경로 강제 설정
    # 파일 구조: IACE_optimized/scheduler/scheduler.py 이므로 부모의 부모가 루트입니다.
    project_root = Path(__file__).resolve().parents[2]
    os.chdir(str(project_root))
    
    print("\n" + "="*60)
    print(f"⏰ 스케줄 분석 시작: {datetime.now()}")
    print("="*60)
    
    try:
        settings = get_settings()
        
        # 1. DB 초기화
        init_db()
        
        # 2. 데이터 수집
        print("\n[1단계] 데이터 수집")
        collector = DataCollector(settings)
        print("  - 입고검사 데이터 수집 중...")
        collected_data = collector.collect_all_data(iqc_file_path=iqc_file_path)
        print("  - 전체 데이터 수집 완료")
        
        # 3. 데이터 전처리
        print("\n[2단계] 데이터 전처리")
        preprocessor = DataPreprocessor()
        
        final_iqc_data = preprocessor.preprocess_pipeline(
            qty_data_month=collected_data['qty_past'],
            defect_data_month=collected_data['defect_past'],
            qty_data_week=collected_data['qty_recent'],
            defect_data_week=collected_data['defect_recent'],
            maker_mapping=collected_data['maker_mapping'],
            vend_cd_df=collected_data['vend_cd_df'],
            iqc_data=collected_data['iqc_data'],
            leave_data=collected_data['leave_data']
        )
        
        # 4. Rule 기반 등급 판정
        print("\n[3단계] Rule 기반 등급 판정")
        rule_engine = QualityRuleEngine(settings)
        rule_result = rule_engine.run_analysis(final_iqc_data)
        
        # 5. ML 분석
        print("\n[4단계] ML 모델 분석")
        ml_analyzer = QualityMLAnalyzer(rule_result, settings)
        ml_result = ml_analyzer.run_analysis()
        
        # ML High Risk 매칭 및 등급 조정
        if ml_result.success and len(ml_result.high_risk_df) > 0:
            ml_high_risk = ml_result.high_risk_df[['PART_CD', 'MAKER_CD', 'VEND_L_CD']].drop_duplicates()
            ml_high_risk['ML_HIGH_RISK'] = True
            
            before_count = len(rule_result[rule_result['PART_Q_LEVEL'] >= 4])
            
            rule_result = rule_result.merge(
                ml_high_risk,
                on=['PART_CD', 'VEND_L_CD'],
                how='left'
            )
            
            # MAKER_CD_x를 MAKER_CD로 복원
            if 'MAKER_CD_x' in rule_result.columns:
                rule_result['MAKER_CD'] = rule_result['MAKER_CD_x']
                rule_result = rule_result.drop(columns=['MAKER_CD_x'], errors='ignore')
            
            rule_result = rule_result.drop(columns=['MAKER_CD_y'], errors='ignore')
            
            mask = rule_result['ML_HIGH_RISK'] == True
            rule_result.loc[mask, 'PART_Q_LEVEL'] = rule_result.loc[mask, 'PART_Q_LEVEL'].apply(lambda x: max(x, 4))
            rule_result.loc[mask, 'LEVEL_REASON'] = '[ML High Risk] AI 모델이 고위험으로 판정'
            
            rule_result = rule_result.drop(columns=['ML_HIGH_RISK'], errors='ignore')
            
            after_count = len(rule_result[rule_result['PART_Q_LEVEL'] >= 4])
            print(f"ML High Risk 매칭으로 등급 조정: {after_count - before_count}개")
        
        # 6. AI 분석
        print("\n[5단계] AI 분석 (Gauss LLM)")
        
        # ML High Risk인 항목만 AI 분석
        ai_target_mask = rule_result['LEVEL_REASON'].str.contains('ML High Risk', na=False)
        ai_target_count = ai_target_mask.sum()
        
        # 기본 컬럼 초기화
        rule_result['AI_SUGGESTED_GRADE'] = rule_result['PART_Q_LEVEL']
        rule_result['AI_CONFIDENCE'] = 0.0
        rule_result['AI_REASONING'] = 'AI 분석 제외'
        
        if ai_target_count > 0:
            try:
                from I_ACE.engine.ai_analyzer import QualityAIAnalyzer
                ai_analyzer = QualityAIAnalyzer(settings)
                ai_target_df = rule_result[ai_target_mask].copy()
            
                print(f"AI 분석 대상: {ai_target_count}건")
                ai_results = ai_analyzer.batch_analyze(ai_target_df)
            
                rule_result.loc[ai_target_mask, 'AI_SUGGESTED_GRADE'] = ai_results['suggested_grade'].values
                rule_result.loc[ai_target_mask, 'AI_CONFIDENCE'] = ai_results['confidence'].values
                rule_result.loc[ai_target_mask, 'AI_REASONING'] = ai_results['reasoning'].values
            except Exception as ai_error:
                # Keep the existing Rule/ML grades if the optional AI stage fails.
                rule_result.loc[ai_target_mask, 'AI_SUGGESTED_GRADE'] = rule_result.loc[ai_target_mask, 'PART_Q_LEVEL']
                rule_result.loc[ai_target_mask, 'AI_CONFIDENCE'] = 0.0
                rule_result.loc[ai_target_mask, 'AI_REASONING'] = 'AI unavailable; Rule/ML grade retained'
                print(f"AI stage unavailable ({type(ai_error).__name__}); continuing with Rule/ML grades")
        
        # 7. 최종 등급 산출
        print("\n[6단계] 하이브리드 최종 등급 산출")
        
        final_grades = []
        final_reasons = []
        level_reasons = []
        
        for idx, row in rule_result.iterrows():
            rule_grade = int(row['PART_Q_LEVEL'])
            ai_grade = int(row['AI_SUGGESTED_GRADE'])
            confidence = float(row['AI_CONFIDENCE'])
            original_reason = row['LEVEL_REASON']
            exclusion_reason = _get_grade_exclusion_reason(row)
            
            if exclusion_reason:
                final_grades.append(3)
                final_reasons.append(exclusion_reason)
                level_reasons.append(exclusion_reason)
                continue
            
            final_grade, final_reason = calculate_final_grade(rule_grade, ai_grade, confidence)
            final_grades.append(final_grade)
            final_reasons.append(final_reason)
            
            if final_grade != rule_grade:
                if final_grade > rule_grade:
                    level_reasons.append(f'[AI분석 등급상향] Rule({rule_grade})→AI({final_grade}), {final_reason}')
                else:
                    level_reasons.append(f'[AI분석 등급하향] Rule({rule_grade})→AI({final_grade}), {final_reason}')
            else:
                level_reasons.append(original_reason)
        
        rule_result['FINAL_GRADE'] = final_grades
        rule_result['FINAL_REASON'] = final_reasons
        rule_result['LEVEL_REASON'] = level_reasons

        # 7.5. 등급 래칫 적용 (악화 즉시 / 개선 단계 하향)
        print("\n[6.5단계] 등급 래칫 적용")

        db = SessionLocal()
        try:
            rule_result = apply_grade_ratchet(rule_result, db, settings)

            # 8. DB 저장
            print("\n[7단계] DB 저장")
            upload_time = datetime.now()
            result = save_analysis_result_to_databases(rule_result, upload_time)
            print(f"Both DB writes completed: {result}")
        finally:
            db.close()

        # 9. 엑셀 파일 저장 (DB 저장 실패해도 실행)
        print("\n[8단계] 엑셀 파일 저장")

        # 파일명: Quality_grade_yymmdd.xlsx (예: Quality_grade_260428.xlsx)
        date_str = datetime.now().strftime('%y%m%d')
        excel_filename = f"Quality_grade_{date_str}.xlsx"
        excel_path = settings.OUTPUT_DIR / excel_filename

        # 결과 폴더가 없으면 생성
        settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # 컬럼 순서 지정 (중복 제거, 불필요한 컬럼 제외)
        column_order = [
            'CATEGORY_L', 'CATEGORY_S', 'PART_NM', 'PART_CD', 'MAKER_CD', 'VEND_L_CD', 'VEND_NM',
            'TEST_LEVEL_CODE', 'TEN_WEEK_INSP_YN',
            'TOTAL_LOT_CNT', 'TOTAL_GR_QTY', 'TOTAL_INSP_QTY', 'TOTAL_INSP_DEF_QTY', 'TOTAL_INSP_PPM', 'TOTAL_FAIL_RATE',
            'MONTH_LOT_CNT', 'MONTH_GR_QTY', 'MONTH_INSP_QTY', 'MONTH_INSP_DEF_QTY', 'MONTH_INSP_PPM', 'MONTH_FAIL_RATE',
            'WEEK_LOT_CNT', 'WEEK_GR_QTY', 'WEEK_INSP_QTY', 'WEEK_INSP_DEF_QTY', 'WEEK_INSP_PPM', 'WEEK_FAIL_RATE',
            'AB_CNT', 'CPK_AB', 'EDGE_AB', 'IF_CD',
            'defect_qty_MONTH', 'ACRS_QTY_MONTH', 'PRODC_PPM_MONTH',
            'defect_qty_WEEK', 'ACRS_QTY_WEEK', 'PRODC_PPM_WEEK', 'ppm_trend_ratio',
            'LEAVE_CNT', 'LEAVE_PROC', 'LEAVE_FAIL_NM',
            'PART_Q_LEVEL', 'LEVEL_REASON',
            'AI_SUGGESTED_GRADE', 'AI_CONFIDENCE', 'AI_REASONING',
            'HYBRID_GRADE', 'FINAL_GRADE', 'FINAL_REASON'
        ]

        # 불필요한 컬럼 제거 (rule_result에서 직접 제거)
        exclude_cols = {c for c in rule_result.columns if c.endswith('.1')}  # TEST_LEVEL_CODE.1 등 중복 컬럼
        exclude_cols.update({'TOTAL_LEVEL', 'MONTH_LEVEL', 'WEEK_LEVEL',
                            'TOTAL_HAS_INSP', 'MONTH_HAS_INSP', 'WEEK_HAS_INSP'})
        rule_result = rule_result.drop(columns=[c for c in exclude_cols if c in rule_result.columns])

        # AB 컬럼이 없으면 추가 (NaN으로 채움)
        for ab_col in ['AB_CNT', 'CPK_AB', 'EDGE_AB']:
            if ab_col not in rule_result.columns:
                rule_result[ab_col] = None

        # 최종 컬럼 선택
        existing_cols = [c for c in column_order if c in rule_result.columns]
        other_cols = [c for c in rule_result.columns if c not in column_order]
        final_order = existing_cols + other_cols

        # 엑셀 저장
        rule_result[final_order].to_excel(excel_path, index=False, engine='openpyxl')
        print(f"✅ 엑셀 저장 완료: {excel_path}")
        print(f"   - 총 {len(rule_result)}건")
        print(f"   - 컬럼 {len(final_order)}개")
        
        # 10. 법인별 불량률 데이터 수집 (parquet 저장)
        print("\n[9단계] 법인별 불량률 데이터 수집")
        try:
            plant_defect_path = save_plant_defect_data()
            print(f"✅ 법인별 불량률 데이터 저장 완료: {plant_defect_path}")
        except Exception as e:
            print(f"⚠️ 법인별 불량률 데이터 수집 실패: {e}")
        
        print("\n" + "="*60)
        print(f"✅ 스케줄 분석 완료: {datetime.now()}")
        print(f"   📁 결과 파일: {excel_path}")
        print("="*60)
        
        return rule_result
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ 스케줄 분석 실패: {e}")
        raise


def start_scheduler(iqc_file_path: str = None, hour: int = 6, minute: int = 0):
    """
    스케줄러 시작
    
    Args:
        iqc_file_path: 입고실적 데이터 파일 경로
        hour: 실행 시간 (시)
        minute: 실행 시간 (분)
    """
    from database.db import scheduler_enabled
    if not scheduler_enabled():
        raise RuntimeError("Batch scheduler is disabled on this server")
    # DB 초기화
    init_db()
    
    # 매일 지정된 시간에 실행 (기본: 오전 6시)
    trigger = CronTrigger(hour=hour, minute=minute, timezone='Asia/Seoul')
    
    scheduler.add_job(
        run_scheduled_analysis,
        trigger=trigger,
        args=[iqc_file_path],
        id='quality_analysis',
        name='품질 등급 분석',
        replace_existing=True
    )
    
    # 스케줄러 시작
    if not scheduler.running:
        scheduler.start()
        print(f"📅 스케줄러 시작: 매일 {hour:02d}:{minute:02d} 실행")
    else:
        print("📅 스케줄러 이미 실행 중")
    
    # 등록된 작업 확인
    jobs = scheduler.get_jobs()
    for job in jobs:
        print(f"   - {job.name}: {job.next_run_time}")


def stop_scheduler():
    """스케줄러 중지"""
    if scheduler.running:
        scheduler.shutdown()
        print("📅 스케줄러 중지")


def run_now(iqc_file_path: str = None):
    """
    즉시 분석 실행 (테스트용)
    
    Args:
        iqc_file_path: 입고실적 데이터 파일 경로
    """
    return run_scheduled_analysis(iqc_file_path)


if __name__ == "__main__":
    # 직접 실행 시 즉시 분석
    print("즉시 분석 실행...")
    run_now()
