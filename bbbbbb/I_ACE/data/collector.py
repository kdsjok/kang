"""
데이터 수집 모듈
- GMQIS API에서 공정/불량 데이터 수집
- 입고실적 데이터 로드
- 협력사 매핑 데이터 로드
"""

import time
import json
import pandas as pd
import requests
from datetime import datetime, timedelta, date
from typing import Tuple, List, Optional, Dict, Any
from dataclasses import dataclass

from I_ACE.config.settings import get_settings


@dataclass
class CollectionResult:
    """데이터 수집 결과"""
    success: bool
    data: Any = None
    error_message: str = ""
    elapsed_time: float = 0.0


class DataCollector:
    """
    품질 데이터 수집기
    - GMQIS API 연동
    - 입고검사 데이터 수집
    - 협력사 데이터 로드
    """
    
    def __init__(self, settings=None):
        """
        초기화
        
        Args:
            settings: 설정 객체 (None이면 기본 설정 사용)
        """
        self.settings = settings or get_settings()
        self.settings.setup_environment()
        
        # 협력사 매핑 데이터 로드
        self._vend_cd_df: Optional[pd.DataFrame] = None
        self._maker_abbr_df: Optional[pd.DataFrame] = None
    
    @property
    def vend_cd_df(self) -> pd.DataFrame:
        """협력사 코드 매핑 (DB 기반, 이제 불필요)"""
        # collect_production_data에서 이미 VEND_CODE를 TAPS vendor_code로 매핑함
        return pd.DataFrame()
    
    @property
    def maker_abbr_df(self) -> pd.DataFrame:
        """제조사 약어 매핑 (DB 기반, 이제 불필요)"""
        # collect_production_data에서 이미 VEND_CODE를 TAPS vendor_code로 매핑함
        return pd.DataFrame()

    @property
    def maker_mapping_dict(self) -> Dict[str, str]:
        """MAKER_ABBR → MAKER_CD 매핑 (DB 기반, 더 이상 불필요)"""
        # TAPS DB에서 직접 매핑되므로 빈 dict 반환
        return {}
    
    def load_iqc_data(self, file_path: Optional[str] = None) -> pd.DataFrame:
        """
        입고실적 데이터 로드 (서브프로세스 방식)
        insp_data.py를 별도 프로세스로 실행하여 결과를 파일로 받음
        """
        import subprocess
        import os
        import sys
        import time
        
        # 결과 파일 경로
        current_dir = str(self.settings.BASE_DIR / "data")
        result_file = os.path.join(current_dir, "iqc_result.parquet")
        insp_data_script = os.path.join(current_dir, "insp_data.py")
        
        try:
            print("[Step] insp_data.py 서브프로세스 실행...")
            
            # 현재 Python 인터프리터 경로 사용 (가상환경 유지)
            python_executable = sys.executable
            
            # --- [프록시 제거 추가] ---
            # 1. 현재 시스템 환경 변수 복사
            clean_env = os.environ.copy()
            
            # 2. 프록시 관련 변수들 목록 (대소문자 포함)
            proxy_vars = [
                'http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 
                'ftp_proxy', 'FTP_PROXY', 'all_proxy', 'ALL_PROXY', 'no_proxy', 'NO_PROXY'
            ]
            
            # 3. 자식 프로세스에게 전달할 환경에서 프록시 제거
            # (Selenium Manager의 외부망 접속 시도와 내부망 호출 간의 충돌 방지)
            for var in proxy_vars:
                if var in clean_env:
                    del clean_env[var]
            # --- [추가 끝] ---
            
            # insp_data.py를 서브프로세스로 실행
            # 결과를 parquet 파일로 저장하도록 인자 전달
            run_started_at = time.time()
            result = subprocess.run(
                [python_executable, insp_data_script, "--output", result_file],
                capture_output=True,
                text=True,
                timeout=600,  # 10분 타임아웃
                cwd=current_dir,  # 작업 디렉토리를 data 폴더로 설정
                env=clean_env  # 프록시 제거된 환경 변수 전달
            )
            
            # 실행 결과 출력
            if result.stdout:
                print(result.stdout)
            if result.stderr:
                print(f"[stderr] {result.stderr}")
            
            if result.returncode != 0:
                print(f"⚠️ insp_data.py 종료 코드: {result.returncode}")

            # 결과 파일 확인: 이번 실행에서 정상 생성된 파일만 사용
            if (
                result.returncode == 0
                and os.path.exists(result_file)
                and os.path.getmtime(result_file) >= run_started_at
            ):
                iqc_df = pd.read_parquet(result_file)
                if not iqc_df.empty:
                    # 전처리기가 대문자 컬럼을 사용하므로 확실히 변환
                    iqc_df.columns = iqc_df.columns.str.upper()
                    
                    # ACE 데이터 컬럼이 없으면 추가 (기본값 None)
                    ace_columns = ['AB_CNT', 'EDGE_AB', 'CPK_AB', 'IF_CD', 'TEST_LEVEL_CODE', 'TEN_WEEK_INSP_YN']
                    for col in ace_columns:
                        if col not in iqc_df.columns:
                            iqc_df[col] = None
                            print(f"[INFO] {col} 컬럼 추가 (기본값: None)")
                    
                    print(f"✅ 입고검사 데이터 로드 완료: {iqc_df.shape[0]}건")
                    return iqc_df
                else:
                    print("⚠️ 결과 파일이 비어있습니다.")
            else:
                print(f"⚠️ 이번 실행에서 유효한 결과 파일이 생성되지 않았습니다: {result_file}")
                
        except subprocess.TimeoutExpired:
            print("⚠️ insp_data.py 실행 타임아웃 (10분 초과)")
        except Exception as e:
            print(f"⚠️ 서브프로세스 실행 실패: {e}")
        
        # 기존 iqc_result.parquet 자동 fallback은 stale 데이터 저장을 유발할 수 있어 사용하지 않는다.
        if os.path.exists(result_file):
            print(f"⚠️ 기존 결과 파일은 새 실행 결과가 아니므로 자동 사용하지 않습니다: {result_file}")
        
        # Fallback 2: 지정된 파일 로드 시도
        if file_path and os.path.exists(file_path):
            print(f"🔄 Fallback: 지정된 엑셀 로드 시도 -> {file_path}")
            df = pd.read_excel(file_path, engine='openpyxl')
            df.columns = df.columns.str.upper()
            return df
        
        # Fallback 3: 최종 실패 시 '컬럼 구조만 있는 빈 DF' 반환 (KeyError 방지)
        print("❌ 로드할 수 있는 데이터가 없어 빈 구조의 데이터프레임을 반환합니다.")
        # 전처리 단계에서 merge 시 필요한 최소 컬럼 정의
        return pd.DataFrame(columns=[
            'CATEGORY_L', 'CATEGORY_S', 'PART_NM', 'PART_CD', 'MAKER_CD', 
            'VEND_L_CD', 'VEND_NM', 'TEST_LEVEL_CODE', 'TEN_WEEK_INSP_YN', 'TOTAL_LOT_CNT'
        ])

    
    def _fetch_api_data(
        self, 
        url: str, 
        payload: Dict, 
        date_str: str, 
        type_name: str
    ) -> Tuple[List, List]:
        """
        API 데이터 요청 (내부 메서드)
        
        Args:
            url: API URL
            payload: 요청 payload
            date_str: 요청 날짜 문자열
            type_name: 데이터 타입명 (에러 로그용)
        
        Returns:
            (데이터 리스트, 에러 로그 리스트)
        """
        errors = []
        data = []
        
        try:
            response = requests.post(url, json=payload, timeout=30)
            
            if response.status_code == 200:
                json_data = response.json()
                rtn_list = json_data.get('rtnList')
                
                if isinstance(rtn_list, list):
                    data = rtn_list
                elif rtn_list is not None:
                    data = [rtn_list]
            else:
                errors.append(f"[{date_str}] {type_name} 요청 실패 (상태 코드: {response.status_code})")
                
        except requests.Timeout:
            errors.append(f"[{date_str}] {type_name} 요청 타임아웃")
        except json.JSONDecodeError:
            errors.append(f"[{date_str}] {type_name} JSON 파싱 에러")
        except Exception as e:
            errors.append(f"[{date_str}] {type_name} 요청 오류: {str(e)}")
        
        return data, errors
    
    def collect_production_data(
        self,
        start_date: date,
        end_date: Optional[date] = None
    ) -> Tuple[List[Dict], List[Dict], List[str]]:
        """
        TAPS DB에서 공정 데이터 수집 (수량, 불량)
        - PLM BOM으로 모델 연결 (defect-trend 동일 로직)
        - vendor_code 매핑 (TAPS t_corp_map 활용)

        Args:
            start_date: 시작 날짜
            end_date: 종료 날짜 (None이면 오늘)

        Returns:
            (수량 데이터 리스트, 불량 데이터 리스트, 에러 로그 리스트)
        """
        from sqlalchemy import create_engine

        start_time = time.time()

        if end_date is None:
            end_date = datetime.now().date()

        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        print(f"공정 데이터 추출 시작 (TAPS DB): {start_str} ~ {end_str}")

        qty_data = []
        defect_data = []
        errors = []

        try:
            # TAPS DB 연결 (mqis_db.py 설정 활용)
            from data.taps_config import get_taps_connection_url

            taps_url = get_taps_connection_url()
            engine = create_engine(taps_url, pool_pre_ping=True)

            # 불량 데이터 조회
            defect_query = f"""
            SELECT * FROM tapsuser.gmqis_detl_d
            WHERE deft_cre_ymd >= {start_str} AND deft_cre_ymd <= {end_str}
            """
            with engine.connect() as conn:
                defect_df = pd.read_sql(defect_query, conn)

            # 컬럼명 대문자 통일 (기존 코드와 호환)
            defect_df.columns = defect_df.columns.str.upper()

            # OUTS_YN = 'N' (사내생산만) 필터
            if 'OUTS_YN' in defect_df.columns:
                before_filter = len(defect_df)
                defect_df = defect_df[defect_df['OUTS_YN'] == 'N'].copy()
                after_filter = len(defect_df)
                print(f"  ℹ️ OUTS_YN 필터: {before_filter} → {after_filter}행 (사내생산만)")

            # 생산 데이터 조회
            manufacturing_query = f"""
            SELECT * FROM tapsuser.gmqis_sumr_d
            WHERE acrs_ymd >= {start_str} AND acrs_ymd <= {end_str}
            """
            with engine.connect() as conn:
                manufacturing_df = pd.read_sql(manufacturing_query, conn)

            # 컬럼명 대문자 통일 (기존 코드와 호환)
            manufacturing_df.columns = manufacturing_df.columns.str.upper()

            # OUTS_YN = 'N' (사내생산만) 필터
            if 'OUTS_YN' in manufacturing_df.columns:
                before_filter = len(manufacturing_df)
                manufacturing_df = manufacturing_df[manufacturing_df['OUTS_YN'] == 'N'].copy()
                after_filter = len(manufacturing_df)
                print(f"  ℹ️ 생산 OUTS_YN 필터: {before_filter} → {after_filter}행 (사내생산만)")

            print(f"✓ TAPS DB 조회: 불량 {len(defect_df)}행, 생산 {len(manufacturing_df)}행")

            # PLM BOM으로 모델 연결 (모델이 비어있는 불량)
            if not defect_df.empty and not manufacturing_df.empty:
                defect_df = self._assign_missing_models_by_bom(defect_df, manufacturing_df)

            # vendor_code 매핑 (2단계: VEND_CODE → VENDOR_CODE → VEND_L_CD)
            if not defect_df.empty:
                # Step 1: TAPS t_corp_map으로 VEND_CODE → VENDOR_CODE 매핑
                vendor_code_map = self._fetch_vendor_code_mapping(engine)
                if vendor_code_map:
                    defect_df['VENDOR_CODE'] = (
                        defect_df['VEND_CODE']
                        .fillna('')
                        .astype(str)
                        .str.strip()
                        .str.upper()
                        .map(vendor_code_map)
                    )
                else:
                    defect_df['VENDOR_CODE'] = defect_df['VEND_CODE']

                # Step 2: IQC tbi_vendor_info로 VENDOR_CODE → VEND_L_CD 매핑
                vendor_l_cd_map = self._fetch_vendor_l_cd_mapping()
                if vendor_l_cd_map:
                    defect_df['VEND_L_CD'] = (
                        defect_df['VENDOR_CODE']
                        .fillna('')
                        .astype(str)
                        .str.strip()
                        .str.upper()
                        .map(vendor_l_cd_map)
                    )
                    mapped_count = int((defect_df['VEND_L_CD'].notna()).sum())
                    print(f"  ✓ 협력사 매핑 완료: {mapped_count}건 (VEND_CODE → VENDOR_CODE → VEND_L_CD)")
                else:
                    print(f"  ⚠️ 협력사 매핑 실패: IQC tbi_vendor_info 조회 불가")

            # DataFrame을 dict 리스트로 변환
            if not manufacturing_df.empty:
                qty_data = manufacturing_df.to_dict('records')

                # 디버깅: 생산 데이터 저장 (최근 데이터만)
                if start_str.startswith('202608'):
                    debug_file_mfg = self.settings.BASE_DIR / "data" / f"debug_manufacturing_{start_str}_{end_str}.csv"
                    manufacturing_df.to_csv(debug_file_mfg, index=False, encoding='utf-8')
                    print(f"  💾 생산 데이터 저장: {debug_file_mfg}")

            if not defect_df.empty:
                defect_data = defect_df.to_dict('records')

                # 디버깅: 불량 데이터를 CSV로 저장 (최근 데이터만)
                if start_str.startswith('202608'):
                    debug_file = self.settings.BASE_DIR / "data" / f"debug_defect_{start_str}_{end_str}.csv"
                    defect_df.to_csv(debug_file, index=False, encoding='utf-8')
                    print(f"  💾 불량 데이터 저장: {debug_file}")

            elapsed = time.time() - start_time
            print(f"✅ {start_str}부터 {end_str}까지 데이터 추출 완료 ({elapsed:.2f}초)")

        except Exception as e:
            errors.append(f"TAPS DB 조회 실패: {str(e)}")
            print(f"❌ {errors[0]}")

        return qty_data, defect_data, errors

    def _assign_missing_models_by_bom(self, defect_df: pd.DataFrame, manufacturing_df: pd.DataFrame) -> pd.DataFrame:
        """PLM BOM을 이용하여 모델이 비어있는 불량을 실제 생산량이 가장 큰 모델로 연결"""
        from database.db import engine as iqc_engine
        from sqlalchemy import text

        result = defect_df.copy()

        print(f"  💾 수집된 데이터 컬럼: {result.columns.tolist()}")

        # 모델이 비어있는 행 식별
        if 'BASIC_MODEL_CODE' not in result.columns:
            print(f"  ⚠️ BASIC_MODEL_CODE 컬럼 없음, 그대로 반환")
            return result

        basic_model = result['BASIC_MODEL_CODE'].fillna('').astype(str).str.strip().str.upper()
        missing_mask = basic_model.isin({'', '-', 'NAN', 'NONE'})

        if not missing_mask.any():
            return result

        # 비어있는 행의 MODEL_CODE에서 BOM 후보 조회
        model_codes = sorted({
            code for code in result.loc[missing_mask, 'MODEL_CODE'].fillna('').astype(str).str.strip().str.upper()
            if code and code not in {'-', 'NAN', 'NONE'}
        })

        if not model_codes:
            return result

        # IQC DB의 PLM BOM에서 모델 후보 조회
        try:
            bom_query = text("""
            SELECT upper(trim(hrnk_matr_code)) AS hrnk_matr_code,
                   upper(trim(model_matr_code)) AS model_matr_code
            FROM tbi_plm_bom_history
            WHERE upper(trim(hrnk_matr_code)) = ANY(:model_codes)
              AND model_matr_code IS NOT NULL
              AND trim(model_matr_code) <> ''
            """)

            with iqc_engine.connect() as conn:
                bom_rows = conn.execute(bom_query, {'model_codes': model_codes}).fetchall()

            candidates = {}
            for row in bom_rows:
                candidates.setdefault(row.hrnk_matr_code, set()).add(row.model_matr_code)

            if not candidates:
                return result

            # 생산 데이터에서 생산량이 가장 큰 모델로 매핑
            manufacturing = manufacturing_df.copy()
            manufacturing['_PROD_QTY'] = pd.to_numeric(manufacturing['ACRS_QTY'], errors='coerce').fillna(0)
            manufacturing = manufacturing.groupby(
                ['PLANT_CODE', 'ACRS_YMD', 'PROC_TYPE_NM', 'BASIC_MODEL_CODE'],
                as_index=False
            )['_PROD_QTY'].sum()

            assigned_count = 0
            for idx, row in result.loc[missing_mask].iterrows():
                source_code = str(row['MODEL_CODE'] or '').strip().upper()
                model_cands = candidates.get(source_code, set())

                if not model_cands:
                    continue

                if len(model_cands) == 1:
                    result.at[idx, 'BASIC_MODEL_CODE'] = next(iter(model_cands))
                    assigned_count += 1
                    continue

                # 생산 이력이 가장 많은 모델 선택
                defect_date = int(str(row.get('DEFT_CRE_YMD', '')).strip() or '0')
                ranked = sorted(
                    [
                        (
                            cand,
                            manufacturing[
                                (manufacturing['PLANT_CODE'] == str(row['PLANT_CODE']).strip().upper())
                                & (manufacturing['ACRS_YMD'] == defect_date)
                                & (manufacturing['PROC_TYPE_NM'] == str(row.get('PROC_TYPE_NM', '')).strip().upper())
                                & (manufacturing['BASIC_MODEL_CODE'].str.startswith(cand))
                            ]['_PROD_QTY'].sum()
                        )
                        for cand in model_cands
                    ],
                    key=lambda x: (-x[1], x[0])
                )

                if ranked and ranked[0][1] > 0:
                    result.at[idx, 'BASIC_MODEL_CODE'] = ranked[0][0]
                    assigned_count += 1

            if assigned_count:
                print(f"✓ PLM BOM 기반 모델 연결: {assigned_count}건")
                # 자세한 데이터 출력
                cols_to_show = ['PRODC_MAGT_NO', 'MODEL_CODE', 'BASIC_MODEL_CODE', 'DEFT_CRE_YMD', 'DEFT_QTY', 'PROC_TYPE_NM']
                available_cols = [c for c in cols_to_show if c in result.columns]
                assigned_rows = result.loc[missing_mask & (result['BASIC_MODEL_CODE'].notna()), available_cols]

                if not assigned_rows.empty:
                    print(f"\n  📌 처리된 불량 데이터 예시 (최대 10건):")
                    print(f"  {'관리번호':<15} {'부품코드':<15} {'모델':<15} {'날짜':<10} {'수량':<6} {'공정':<10}")
                    print(f"  {'-'*80}")
                    for idx, (_, row) in enumerate(assigned_rows.head(10).iterrows()):
                        prodc_no = str(row['PRODC_MAGT_NO'])[:12] if 'PRODC_MAGT_NO' in row else "N/A"
                        print(f"  {prodc_no:<15} {row['MODEL_CODE']:<15} {row['BASIC_MODEL_CODE']:<15} {row['DEFT_CRE_YMD']:<10} {row['DEFT_QTY']:<6.0f} {row['PROC_TYPE_NM']:<10}")

        except Exception as e:
            print(f"⚠️ PLM BOM 모델 연결 실패: {e}")

        return result

    def _fetch_vendor_code_mapping(self, taps_engine) -> Dict[str, str]:
        """TAPS DB에서 vendor_code 매핑 조회 (VEND_CODE → VENDOR_CODE)"""
        from sqlalchemy import text

        try:
            query = text("""
            SELECT upper(trim(main.maker_code)) AS vend_code,
                   upper(trim(member.dealcorp_code)) AS vendor_code
            FROM tapsuser.t_corp_map_main main
            JOIN tapsuser.t_corp_map_member member ON member.map_id = main.map_id
            WHERE nvl(main.del_is, 'N') <> 'Y'
              AND nvl(member.del_is, 'N') <> 'Y'
            """)

            with taps_engine.connect() as conn:
                mappings = pd.read_sql_query(query, conn)

            if mappings.empty:
                return {}

            # 중복 제거 (vend_code별 첫 매핑)
            mappings.columns = [str(col).upper() for col in mappings.columns]
            mappings = mappings.drop_duplicates('VEND_CODE', keep='first')

            return dict(zip(mappings['VEND_CODE'], mappings['VENDOR_CODE']))

        except Exception as e:
            print(f"⚠️ TAPS vendor_code 매핑 실패: {e}")
            return {}

    def _fetch_vendor_l_cd_mapping(self) -> Dict[str, str]:
        """IQC DB에서 VEND_L_CD 매핑 조회 (VENDOR_CODE → VEND_L_CD)"""
        from database.db import engine as iqc_engine
        from sqlalchemy import text

        try:
            query = text("""
            SELECT
                upper(trim(vendor_s_cd)) AS vendor_code,
                upper(trim(vendor_g_cd)) AS vend_l_cd
            FROM tbi_vendor_info
            WHERE vendor_s_cd IS NOT NULL
              AND vendor_g_cd IS NOT NULL
            UNION
            SELECT
                upper(trim(vendor_g_cd)) AS vendor_code,
                upper(trim(vendor_g_cd)) AS vend_l_cd
            FROM tbi_vendor_info
            WHERE vendor_g_cd IS NOT NULL
            """)

            with iqc_engine.connect() as conn:
                mappings = pd.read_sql(query, conn)

            if mappings.empty:
                return {}

            # 중복 제거 (vendor_code별 첫 매핑)
            mappings.columns = [str(col).upper() for col in mappings.columns]
            mappings = mappings.drop_duplicates('VENDOR_CODE', keep='first')

            return dict(zip(mappings['VENDOR_CODE'], mappings['VEND_L_CD']))

        except Exception as e:
            print(f"⚠️ IQC VEND_L_CD 매핑 실패: {e}")
            return {}
    
    def collect_leave_data(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> pd.DataFrame:
        """
        불량 이탈 데이터 수집
        
        Args:
            start_date: 시작 날짜 (None이면 14일 전)
            end_date: 종료 날짜 (None이면 오늘)
        
        Returns:
            전처리된 불량 이탈 데이터프레임
        """
        if end_date is None:
            end_date = datetime.now().date()
        if start_date is None:
            start_date = end_date - timedelta(days=self.settings.RECENT_DAYS)
        
        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')
        
        print(f"불량 이탈 데이터 추출 시작: {start_str} ~ {end_str}")
        
        payload = {
            "SYSTEM_GBN": self.settings.API_SYSTEM_GBN_PROC,
            "PRODUCT": self.settings.API_PRODUCT,
            "USER_ID": self.settings.API_USER_ID,
            "SEARCH_FROM": start_str,
            "SEARCH_TO": end_str
        }
        
        try:
            response = requests.post(self.settings.leave_url, json=payload, timeout=30)
            
            if response.status_code == 200:
                json_data = response.json()
                rtn_list = json_data.get('rtnList')
                
                if not rtn_list:
                    print("⚠️ 해당 기간에 수집된 데이터가 없습니다.")
                    return pd.DataFrame()
                
                # 협력사 코드 매핑 (실제 컬럼 구조에 맞춤)
                # Use the same vendor_s_cd -> vendor_g_cd mapping as production data.
                vend_map = self._fetch_vendor_l_cd_mapping()
                leave_data = pd.json_normalize(rtn_list).query("FAIL_ACTION_TYPE_NM != '작업자 오기입'").copy()
                vendor_codes = leave_data['DEALCORP_CODE'].astype('string').str.strip().str.upper()
                vendor_codes = vendor_codes.mask(vendor_codes.eq(''))
                mapped_codes = vendor_codes.map(vend_map).astype('string').str.strip().str.upper()
                mapped_codes = mapped_codes.mask(mapped_codes.eq(''))

                # Match IQC's fallback: retain the source code when no mapping exists.
                # Keep unknown keys visible instead of silently dropping their events.
                leave_data['VEND_L_CD'] = mapped_codes.fillna(vendor_codes)
                unmapped_count = int((mapped_codes.isna() & vendor_codes.notna()).sum())
                missing_count = int(vendor_codes.isna().sum())
                if unmapped_count:
                    print(f"[WARN] Leave vendor mapping missing for {unmapped_count} events; source codes retained")
                if missing_count:
                    print(f"[WARN] Leave vendor code missing for {missing_count} events; retained with unknown vendor")
                leave_data['SEC_CODE'] = leave_data['SEC_CODE'].astype('string').str.strip().str.upper()
                outlier_process = (
                    leave_data.groupby(['SEC_CODE', 'VEND_L_CD'], dropna=False)
                    .agg(
                        LEAVE_CNT=('SEC_CODE', 'size'),
                        LEAVE_PROC=('PROC_NM', lambda x: ', '.join(x.dropna().unique())),
                        LEAVE_FAIL_NM=('FAIL_STATE_NM', lambda x: ', '.join(x.dropna().unique()))
                    )
                    .reset_index()
                )
                
                print("불량 이탈 데이터 추출 및 전처리 완료")
                return outlier_process
                
            else:
                print(f"❌ API 요청 실패 (상태 코드: {response.status_code})")
                return pd.DataFrame()
                
        except Exception as e:
            print(f"❌ 데이터 처리 중 에러 발생: {e}")
            return pd.DataFrame()
    
    def collect_all_data(
        self,
        iqc_file_path: Optional[str] = None,
        custom_date_range: Optional[Tuple[date, date]] = None
    ) -> Dict[str, Any]:
        """
        전체 데이터 수집 (mqis_db + ab_anomaly + 공정 데이터)

        Args:
            iqc_file_path: 입고실적 데이터 파일 경로 (미사용 - mqis_db 직접 호출)
            custom_date_range: 사용자 지정 날짜 범위 (start, end)

        Returns:
            수집된 모든 데이터 딕셔너리
        """
        today = datetime.now().date()

        # 날짜 범위 설정
        if custom_date_range:
            start_recent, end_recent = custom_date_range
        else:
            start_recent = today - timedelta(days=self.settings.RECENT_DAYS)
            end_recent = today

        start_past = today - timedelta(days=self.settings.PAST_DAYS_START)
        end_past = today - timedelta(days=self.settings.PAST_DAYS_END)

        # 데이터 수집
        print("="*60)
        print("데이터 수집 시작")
        print("="*60)

        # [1단계] 입고검사 기초 데이터 (mqis_db.py 직접 호출)
        print("\n[1-1] 입고검사 기초 데이터 수집 (mqis_db.py)...")
        try:
            from I_ACE.data.mqis_db import get_db_data
            iqc_data = get_db_data()
            if iqc_data is not None:
                iqc_data.columns = iqc_data.columns.str.upper()
                print(f"  ✓ 입고검사 데이터: {len(iqc_data)}건")
            else:
                print(f"  ⚠️ 입고검사 데이터 로드 실패, 빈 DF 사용")
                iqc_data = pd.DataFrame()
        except Exception as e:
            print(f"  ⚠️ mqis_db 호출 실패: {e}, 빈 DF 사용")
            iqc_data = pd.DataFrame()

        # [2단계] 이상감지 데이터 (ab_anomaly.py)
        print("\n[1-2] 이상감지 데이터 수집 (ab_anomaly.py)...")
        try:
            from I_ACE.data.ab_anomaly import get_ab_data
            ab_data = get_ab_data()
            if ab_data is not None and not ab_data.empty:
                ab_data.columns = ab_data.columns.str.upper()
                print(f"  ✓ 이상감지 데이터: {len(ab_data)}건")
                # iqc_data와 merge
                if not iqc_data.empty:
                    iqc_data = iqc_data.merge(
                        ab_data,
                        on=['PART_CD', 'VEND_L_CD'],
                        how='left'
                    )
                    print(f"  ✓ 이상감지 데이터 병합 완료")
            else:
                print(f"  ℹ️ 이상감지 데이터 없음 (최근 데이터 미제출)")
        except Exception as e:
            print(f"  ⚠️ ab_anomaly 호출 실패: {e}")

        # [3단계] 공정 데이터 (최근 + 과거)
        print("\n[1-3] 공정 데이터 수집...")
        qty_recent, defect_recent, _ = self.collect_production_data(start_recent, end_recent)
        qty_past, defect_past, _ = self.collect_production_data(start_past, end_past)

        # [4단계] 불량 이탈 데이터
        leave_data = self.collect_leave_data(start_recent, end_recent)

        return {
            'qty_recent': qty_recent,
            'defect_recent': defect_recent,
            'qty_past': qty_past,
            'defect_past': defect_past,
            'leave_data': leave_data,
            'iqc_data': iqc_data,
            'maker_mapping': self.maker_mapping_dict,
            'vend_cd_df': self.vend_cd_df
        }