"""
데이터 전처리 모듈
- PPM 데이터 처리
- 모델 코드/생산라인 변환
- 데이터 병합 및 정제
"""

import pandas as pd
import numpy as np
from typing import Dict, List


class DataPreprocessor:
    """
    품질 데이터 전처리기
    - PPM 계산
    - 모델 코드 변환
    - 데이터 병합
    """
    
    @staticmethod
    def truncate_model_code(model_code: str) -> str:
        """
        모델 코드 변환
        
        Args:
            model_code: 원본 모델 코드
        
        Returns:
            변환된 모델 코드
        """
        if pd.isna(model_code):
            return model_code
        
        model_code = str(model_code)
        
        if model_code.startswith('BSM'):
            return model_code[1:8]  # 8자리
        elif model_code.startswith('SM'):
            return model_code[:7]  # 7자리
        return model_code
    
    @staticmethod
    def truncate_line_nm(line_nm: str) -> str:
        """
        생산라인 명칭 변환
        
        Args:
            line_nm: 원본 라인 명칭
        
        Returns:
            변환된 라인 명칭
        """
        if pd.isna(line_nm):
            return line_nm
        
        line_nm = str(line_nm)
        
        if 'MAIN' in line_nm:
            return 'MAIN'
        elif 'CNC' in line_nm:
            return 'CNC'
        return line_nm
    
    def process_ppm_data(
        self,
        qty_data: List[Dict],
        defect_data: List[Dict],
        mapping_dict: Dict[str, str]
    ) -> pd.DataFrame:
        """
        PPM 데이터 처리 (DEFT_MATR_CODE 기준, 공정별 최대불량 기준 생산수)

        Args:
            qty_data: 수량 데이터 리스트
            defect_data: 불량 데이터 리스트 (DEFT_MATR_CODE, VEND_L_CD, PROC_TYPE_NM 포함)
            mapping_dict: 제조사 코드 매핑 딕셔너리 (사용 안함)

        Returns:
            PPM 계산된 데이터프레임 (DEFT_MATR_CODE, VEND_L_CD, PRODC_PPM_WEEK/MONTH 포함)
        """
        qty_df = pd.DataFrame(qty_data)
        defect_df = pd.DataFrame(defect_data)

        if qty_df.empty or defect_df.empty:
            return pd.DataFrame()

        # [1] 공정 정규화: BLOCK MAIN → MAIN
        defect_df['PROC_TYPE_NM'] = defect_df['PROC_TYPE_NM'].replace('BLOCK MAIN', 'MAIN')
        qty_df['PROC_TYPE_NM'] = qty_df['PROC_TYPE_NM'].replace('BLOCK MAIN', 'MAIN')

        # [2] 대상 공정만 필터링 (SMD, MAIN, SUB)
        target_procs = ['SMD', 'MAIN', 'SUB']
        defect_df = defect_df[defect_df['PROC_TYPE_NM'].isin(target_procs)].copy()
        qty_df = qty_df[qty_df['PROC_TYPE_NM'].isin(target_procs)].copy()

        # [3] DEFT_MATR_CODE(부품코드) 기준으로 그룹핑하여 PPM 계산
        # 부품별/협력사별로 불량과 생산 데이터를 집계
        ppm_results = []

        for (part_code, vend_l_cd), group_defect in defect_df.groupby(['DEFT_MATR_CODE', 'VEND_L_CD']):
            # 불량 데이터가 없으면 스킵
            if group_defect.empty:
                continue

            # [3-1] 공정별 불량수 집계
            proc_defects = group_defect.groupby('PROC_TYPE_NM')['DEFT_QTY'].sum()

            if proc_defects.empty:
                continue

            # [3-2] 불량이 가장 많은 공정 선택
            max_proc = proc_defects.idxmax()
            total_defects = proc_defects.sum()

            # [3-3] 부품에 매핑된 모든 베이스 모델 추출
            all_base_models = group_defect['BASIC_MODEL_CODE'].unique()

            # [3-4] 선택된 공정의 생산수 조회 (모든 모델 버전 포함)
            qty_for_max_proc = 0
            for base_model in all_base_models:
                model_qty = qty_df[
                    (qty_df['BASIC_MODEL_CODE'].str.contains(base_model, na=False, regex=False)) &
                    (qty_df['PROC_TYPE_NM'] == max_proc)
                ]['ACRS_QTY'].sum()
                qty_for_max_proc += model_qty

            # [3-5] PPM 계산
            ppm = (total_defects / qty_for_max_proc * 1_000_000) if qty_for_max_proc > 0 else 0

            ppm_results.append({
                'DEFT_MATR_CODE': part_code,
                'VEND_L_CD': vend_l_cd,
                'PRODC_PPM': ppm,
                'DEFT_QTY': total_defects,
                'ACRS_QTY': qty_for_max_proc,
                'MAX_PROC': max_proc,
                'PROC_COUNT': len(proc_defects)
            })

        if not ppm_results:
            return pd.DataFrame()

        result_df = pd.DataFrame(ppm_results)
        return result_df.sort_values('PRODC_PPM', ascending=False)
        
        result_defect = defect_df.groupby(
            ['DEFT_CRE_YMD', 'BASIC_MODEL_CODE', 'VEND_CODE', 'PROC_TYPE_NM', 'DEFT_MATR_CODE']
        ).size().reset_index(name='defect_qty')
        
        # 병합
        merged = pd.merge(
            result_defect, result_qty,
            on=['DEFT_CRE_YMD', 'PROC_TYPE_NM', 'BASIC_MODEL_CODE'],
            how='left'
        )
        merged = merged.dropna(subset=['ACRS_QTY'])
        
        # 최종 그룹핑 및 PPM 계산
        ppm_df = merged.groupby(
            ['DEFT_MATR_CODE', 'VEND_CODE', 'PROC_TYPE_NM'],
            as_index=False
        )[['defect_qty', 'ACRS_QTY']].sum()
        
        ppm_df['PRODC_PPM'] = np.where(
            ppm_df['ACRS_QTY'] == 0,
            np.nan,
            (ppm_df['defect_qty'] / ppm_df['ACRS_QTY']) * 1000000
        )
        ppm_df['PRODC_PPM'] = ppm_df['PRODC_PPM'].fillna(0).astype(int)
        
        # 조건 필터링
        ppm_df = ppm_df[
            (ppm_df['ACRS_QTY'] > 1000) & 
            (~ppm_df['PROC_TYPE_NM'].isin(['CNC']))
        ]
        
        return ppm_df.sort_values('PRODC_PPM', ascending=False)
    
    @staticmethod
    def _with_period_suffix(ppm_df: pd.DataFrame, period: str) -> pd.DataFrame:
        """공정 PPM 컬럼에 기간 접미사를 붙인다."""
        df = ppm_df.copy()
        rename_map = {
            col: f'{col}_{period}'
            for col in ['defect_qty', 'ACRS_QTY', 'PRODC_PPM']
            if col in df.columns
        }
        return df.rename(columns=rename_map)
    
    @staticmethod
    def _ensure_period_metric_columns(ppm_df: pd.DataFrame) -> pd.DataFrame:
        """월간/주간 공정 PPM 필수 컬럼을 보강한다."""
        df = ppm_df.copy()
        for period in ['MONTH', 'WEEK']:
            for col in ['defect_qty', 'ACRS_QTY', 'PRODC_PPM']:
                period_col = f'{col}_{period}'
                if period_col not in df.columns:
                    df[period_col] = 0
                df[period_col] = pd.to_numeric(df[period_col], errors='coerce').fillna(0)
        if 'ppm_trend_ratio' not in df.columns:
            df['ppm_trend_ratio'] = np.where(
                df['PRODC_PPM_MONTH'] == 0,
                0,
                round(df['PRODC_PPM_WEEK'] / df['PRODC_PPM_MONTH'], 2)
            )
        else:
            df['ppm_trend_ratio'] = pd.to_numeric(df['ppm_trend_ratio'], errors='coerce').fillna(0)
        return df
    
    def _prepare_vend_metrics_for_merge(self, merged_vend_data: pd.DataFrame) -> pd.DataFrame:
        """
        입고검사 데이터에 붙일 공정 PPM 데이터를 부품/협력사 단위로 집계한다.
        left join 전에 1 key = 1 row로 만들어 조인 후 임의 중복 제거가 발생하지 않게 한다.
        """
        cols_vend = [
            'DEFT_MATR_CODE', 'VEND_L_CD',
            'defect_qty_MONTH', 'ACRS_QTY_MONTH', 'PRODC_PPM_MONTH',
            'defect_qty_WEEK', 'ACRS_QTY_WEEK', 'PRODC_PPM_WEEK', 'ppm_trend_ratio'
        ]

        if merged_vend_data.empty:
            return pd.DataFrame(columns=cols_vend)

        # merge_ppm_data()의 반환 컬럼을 소문자로 정규화
        # DEFT_QTY_MONTH/WEEK → defect_qty_MONTH/WEEK로 변환
        vend_data = merged_vend_data.copy()
        if 'DEFT_QTY_MONTH' in vend_data.columns:
            vend_data['defect_qty_MONTH'] = vend_data['DEFT_QTY_MONTH']
        if 'DEFT_QTY_WEEK' in vend_data.columns:
            vend_data['defect_qty_WEEK'] = vend_data['DEFT_QTY_WEEK']

        vend_data = self._ensure_period_metric_columns(vend_data)
        required_keys = ['DEFT_MATR_CODE', 'VEND_L_CD']
        if any(col not in vend_data.columns for col in required_keys):
            return pd.DataFrame(columns=cols_vend)

        qty_cols = ['defect_qty_MONTH', 'ACRS_QTY_MONTH', 'defect_qty_WEEK', 'ACRS_QTY_WEEK']
        grouped = (
            vend_data
            .groupby(required_keys, as_index=False)[qty_cols]
            .sum()
        )

        grouped['PRODC_PPM_MONTH'] = np.where(
            grouped['ACRS_QTY_MONTH'] == 0,
            0,
            (grouped['defect_qty_MONTH'] / grouped['ACRS_QTY_MONTH']) * 1000000
        ).astype(int)
        grouped['PRODC_PPM_WEEK'] = np.where(
            grouped['ACRS_QTY_WEEK'] == 0,
            0,
            (grouped['defect_qty_WEEK'] / grouped['ACRS_QTY_WEEK']) * 1000000
        ).astype(int)
        grouped['ppm_trend_ratio'] = np.where(
            grouped['PRODC_PPM_MONTH'] == 0,
            0,
            round(grouped['PRODC_PPM_WEEK'] / grouped['PRODC_PPM_MONTH'], 2)
        )

        return grouped[cols_vend]
    
    def merge_ppm_data(
        self,
        month_ppm_df: pd.DataFrame,
        week_ppm_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        월간/주간 PPM 데이터 병합 (부품/협력사 단위)

        Args:
            month_ppm_df: 월간 PPM 데이터 (DEFT_MATR_CODE, VEND_L_CD 기준)
            week_ppm_df: 주간 PPM 데이터 (DEFT_MATR_CODE, VEND_L_CD 기준)

        Returns:
            병합된 PPM 데이터 (PRODC_PPM_WEEK, PRODC_PPM_MONTH, ppm_trend_ratio 포함)
        """
        # 월간/주간 데이터에 suffix 추가 (중복 컬럼 제거)
        # Empty periods still need join keys so every input uses the same merge path.
        empty_columns = ['DEFT_MATR_CODE', 'VEND_L_CD', 'DEFT_QTY', 'ACRS_QTY', 'PRODC_PPM']
        month_ppm_df = (pd.DataFrame(columns=empty_columns)
                        if month_ppm_df.empty else month_ppm_df.copy())
        week_ppm_df = (pd.DataFrame(columns=empty_columns)
                       if week_ppm_df.empty else week_ppm_df.copy())

        # 병합용 키 컬럼만 남기고 나머지는 suffix 추가
        month_keep_cols = ['DEFT_MATR_CODE', 'VEND_L_CD']
        month_rename_cols = {col: f'{col}_MONTH' for col in month_ppm_df.columns if col not in month_keep_cols}
        month_ppm_df = month_ppm_df.rename(columns=month_rename_cols)

        week_keep_cols = ['DEFT_MATR_CODE', 'VEND_L_CD']
        week_rename_cols = {col: f'{col}_WEEK' for col in week_ppm_df.columns if col not in week_keep_cols}
        week_ppm_df = week_ppm_df.rename(columns=week_rename_cols)

        # DEFT_MATR_CODE, VEND_L_CD 기준으로 병합
        prodc_ppm = pd.merge(
            month_ppm_df,
            week_ppm_df,
            on=['DEFT_MATR_CODE', 'VEND_L_CD'],
            how='outer'
        )

        # 누락된 컬럼 채우기
        for col in ['PRODC_PPM_WEEK', 'PRODC_PPM_MONTH', 'DEFT_QTY_WEEK', 'DEFT_QTY_MONTH', 'ACRS_QTY_WEEK', 'ACRS_QTY_MONTH']:
            if col not in prodc_ppm.columns:
                prodc_ppm[col] = 0

        prodc_ppm['PRODC_PPM_WEEK'] = prodc_ppm['PRODC_PPM_WEEK'].fillna(0)
        prodc_ppm['PRODC_PPM_MONTH'] = prodc_ppm['PRODC_PPM_MONTH'].fillna(0)
        prodc_ppm['DEFT_QTY_WEEK'] = prodc_ppm['DEFT_QTY_WEEK'].fillna(0).astype(int)
        prodc_ppm['DEFT_QTY_MONTH'] = prodc_ppm['DEFT_QTY_MONTH'].fillna(0).astype(int)
        prodc_ppm['ACRS_QTY_WEEK'] = prodc_ppm['ACRS_QTY_WEEK'].fillna(0).astype(int)
        prodc_ppm['ACRS_QTY_MONTH'] = prodc_ppm['ACRS_QTY_MONTH'].fillna(0).astype(int)

        # PPM 추세 계산 (WEEK / MONTH)
        prodc_ppm['ppm_trend_ratio'] = np.where(
            prodc_ppm['PRODC_PPM_MONTH'] == 0,
            0,
            round(prodc_ppm['PRODC_PPM_WEEK'] / prodc_ppm['PRODC_PPM_MONTH'], 2)
        )

        return prodc_ppm
    
    def merge_vend_data(
        self,
        prodc_ppm: pd.DataFrame,
        vend_cd_df: pd.DataFrame
    ) -> pd.DataFrame:
        """
        협력사 데이터 병합
        
        Args:
            prodc_ppm: PPM 데이터 (VEND_CODE는 MAKER_CD로 매핑된 상태)
            vend_cd_df: 협력사 코드 데이터 (vendor.parquet)
        
        Returns:
            협력사 정보가 병합된 데이터
        """
        # vendor.parquet 구조 확인
        # 예상 컬럼: vend_l_cd, vend_l_nm, maker_cd
        # 또는: VEND_S_CD, VEND_S_NM, VEND_L_CD, VEND_L_NM
        
        if 'maker_cd' in vend_cd_df.columns:
            # VEND_CODE(=MAKER_CD)와 vendor.parquet의 maker_cd를 매칭하여 vend_l_cd 가져오기
            merged = pd.merge(
                prodc_ppm, 
                vend_cd_df,
                left_on=['VEND_CODE'],
                right_on=['maker_cd'],
                how='left'
            )
            # 매핑되지 않은 경우 VEND_CODE를 그대로 VEND_L_CD로 사용
            merged['VEND_L_CD'] = merged['vend_l_cd'].fillna(merged['VEND_CODE'])
            # 불필요한 컬럼 제거
            merged = merged.drop(columns=['vend_l_cd', 'vend_l_nm', 'maker_cd'], errors='ignore')
        elif 'VEND_S_CD' in vend_cd_df.columns:
            # 기존 방식: VEND_S_CD 기준 매핑
            merged = pd.merge(
                prodc_ppm, vend_cd_df,
                left_on=['VEND_CODE'],
                right_on=['VEND_S_CD'],
                how='left'
            )
            merged['VEND_L_CD'] = merged['VEND_L_CD'].fillna(merged['VEND_CODE'])
            merged = merged.drop(columns=['VEND_S_CD', 'VEND_S_NM', 'VEND_L_NM'], errors='ignore')
        else:
            # 매핑할 컬럼이 없으면 VEND_CODE를 그대로 VEND_L_CD로 사용
            merged = prodc_ppm.copy()
            merged['VEND_L_CD'] = merged['VEND_CODE']
        
        return merged
    
    def create_final_iqc_data(
        self,
        iqc_data: pd.DataFrame,
        merged_vend_data: pd.DataFrame,
        leave_data: pd.DataFrame
    ) -> pd.DataFrame:
        """
        최종 입고검사 데이터 생성
        
        Args:
            iqc_data: 입고검사 원본 데이터
            merged_vend_data: 병합된 협력사 PPM 데이터
            leave_data: 불량 이탈 데이터
        
        Returns:
            최종 병합된 입고검사 데이터
        """
        cols_outlier = [
            'SEC_CODE', 'VEND_L_CD',
            'LEAVE_CNT', 'LEAVE_PROC', 'LEAVE_FAIL_NM'
        ]
        vend_metrics = self._prepare_vend_metrics_for_merge(merged_vend_data)
        leave_metrics = (
            leave_data[cols_outlier]
            if not leave_data.empty
            else pd.DataFrame(columns=cols_outlier)
        )
        
        before_rows = len(iqc_data)
        final_iqc_data = (
            iqc_data
            .merge(
                vend_metrics,
                left_on=['PART_CD', 'VEND_L_CD'],
                right_on=['DEFT_MATR_CODE', 'VEND_L_CD'],
                how='left'
            )
            .drop(columns=['DEFT_MATR_CODE'], errors='ignore')
            .merge(
                leave_metrics,
                left_on=['PART_CD', 'VEND_L_CD'],
                right_on=['SEC_CODE', 'VEND_L_CD'],
                how='left'
            )
            .drop(columns=['SEC_CODE'], errors='ignore')
        )
        
        # left join은 원본 row를 삭제하지 않는다. 단, 원본/조인 데이터 중복은 명시 키로만 정리한다.
        dedupe_keys = [col for col in ['PART_CD', 'MAKER_CD', 'VEND_L_CD'] if col in final_iqc_data.columns]
        if dedupe_keys:
            joined_rows = len(final_iqc_data)
            final_iqc_data = final_iqc_data.drop_duplicates(subset=dedupe_keys, keep='first')
            if len(final_iqc_data) < before_rows:
                print(
                    f"⚠️ 중복 제거 후 row 감소: {before_rows} -> {len(final_iqc_data)} "
                    f"(keys={dedupe_keys})"
                )
            elif joined_rows > before_rows:
                print(
                    f"ℹ️ 조인 중복 row 정리: {joined_rows} -> {len(final_iqc_data)} "
                    f"(keys={dedupe_keys})"
                )
        
        return final_iqc_data
    
    def preprocess_pipeline(
        self,
        qty_data_month: List[Dict],
        defect_data_month: List[Dict],
        qty_data_week: List[Dict],
        defect_data_week: List[Dict],
        maker_mapping: Dict[str, str],
        vend_cd_df: pd.DataFrame,
        iqc_data: pd.DataFrame,
        leave_data: pd.DataFrame
    ) -> pd.DataFrame:
        """
        전체 전처리 파이프라인
        
        Args:
            qty_data_month: 월간 수량 데이터
            defect_data_month: 월간 불량 데이터
            qty_data_week: 주간 수량 데이터
            defect_data_week: 주간 불량 데이터
            maker_mapping: 제조사 매핑
            vend_cd_df: 협력사 코드 데이터
            iqc_data: 입고검사 데이터
            leave_data: 불량 이탈 데이터
        
        Returns:
            전처리된 최종 데이터
        """
        print("데이터 프레임형태 변환 시작")
        
        # PPM 데이터 처리
        month_ppm_df = self.process_ppm_data(qty_data_month, defect_data_month, maker_mapping)
        week_ppm_df = self.process_ppm_data(qty_data_week, defect_data_week, maker_mapping)
        
        if month_ppm_df.empty and week_ppm_df.empty:
            print("⚠️ PPM 데이터가 없습니다.")
        # Preserve quantities and period suffixes for both/full/empty periods alike.
        prodc_ppm = self.merge_ppm_data(month_ppm_df, week_ppm_df)

        # 주의: 새로운 로직에서는 VEND_L_CD가 이미 매핑되어 있으므로
        # 협력사 데이터 병합(merge_vend_data)을 건너뜀
        # merged_vend_data = self.merge_vend_data(prodc_ppm, vend_cd_df)

        # 최종 데이터 생성
        final_data = self.create_final_iqc_data(iqc_data, prodc_ppm, leave_data)
        
        print("데이터 프레임형태 변환 종료")
        
        return final_data