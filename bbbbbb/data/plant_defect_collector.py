"""
입고검사 데이터 수집 모듈
- TAPS DB에서 입고검사 데이터 추출
- insp_data.parquet 파일로 저장
- PLANT별 / CATEGORY_L별 그룹핑 지원
"""

import pandas as pd
from sqlalchemy import create_engine
from pathlib import Path
from dotenv import load_dotenv
from datetime import datetime
import os

# 1. .env 파일의 경로를 구하고 환경 변수로 로드 (이 과정이 반드시 선행되어야 합니다!)
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

# TAPS DB 연결 정보
TAPS_DB_USER = os.environ.get("TAPS_DB_USER")
TAPS_DB_PASSWORD = os.environ.get("TAPS_DB_PASSWORD")
TAPS_DB_HOST = os.environ.get("TAPS_DB_HOST")
TAPS_DB_PORT = os.environ.get("TAPS_DB_PORT")
TAPS_DB_NAME = os.environ.get("TAPS_DB_NAME")

def get_insp_data() -> pd.DataFrame:
    """
    입고검사 원본 데이터를 TAPS DB에서 추출
    
    Returns:
        DataFrame: PLANT + CATEGORY_L별 입고검사 데이터
    """
    db_url = f"postgresql://{TAPS_DB_USER}:{TAPS_DB_PASSWORD}@{TAPS_DB_HOST}:{TAPS_DB_PORT}/{TAPS_DB_NAME}"
    engine = create_engine(db_url)
    
    query = """
    with RawData AS (
    -- 1. 기초 데이터 추출
    SELECT 
        A.PLANT, A.MAKER_CD, A.pjt_nm,
        A.SEC_CODE, A.ITEM_GRP_CODE, 
        TAPSUSER.FN_QCODE_NAME2(TAPSUSER.FN_GET_PARENT_CD(A.ITEM_GRP_CODE),'ko') AS CATEGORY_L,
		TAPSUSER.FN_QCODE_NAME2(A.ITEM_GRP_CODE, 'ko') AS CATEGORY_S,
        A.UD_CD, A.INSP_STATUS, A.GR_DATE,
        A.GR_QTY, A.SAM_CNT, A.REJECT_CNT
    FROM TAPSUSER.T_SQCI_IPGO_INSP A
     WHERE A.gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 year', 'YYYYMMDD')
       AND A.INSP_STATUS IS NOT NULL 
       AND TAPSUSER.FN_GET_PLANT_GBN(A.PLANT) = UPPER('TELE')
       AND TAPSUSER.FN_QCODE_NAME2(A.PLANT,'ko') <> 'SEV_RB'
       AND TAPSUSER.FN_QCODE_NAME2(A.ITEM_GRP_CODE, 'ko') NOT IN ('SVC','SKD(MX)')
),
Summary AS (
    SELECT 
        PLANT, pjt_nm, CATEGORY_L, CATEGORY_S, SEC_CODE, MAKER_CD,
        COUNT(*) AS TOTAL_LOT_CNT,
        SUM(GR_QTY) AS TOTAL_GR_QTY,
        SUM(SAM_CNT) AS TOTAL_INSP_QTY,
        SUM(REJECT_CNT) AS TOTAL_INSP_DEF_QTY,
        SUM(CASE WHEN UD_CD = 'MQ0949' AND INSP_STATUS = 'MQ1102' THEN 1 ELSE 0 END) AS TOTAL_PASS_LOT,
        SUM(CASE WHEN UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS TOTAL_FAIL_LOT,
        -- 최근 2주 (WEEK_)
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN 1 ELSE 0 END) AS WEEK_LOT_CNT,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN GR_QTY ELSE 0 END) AS WEEK_GR_QTY,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN SAM_CNT ELSE 0 END) AS WEEK_INSP_QTY,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN REJECT_CNT ELSE 0 END) AS WEEK_INSP_DEF_QTY,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') AND UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS WEEK_FAIL_LOT,
        -- 최근 1개월 (MONTH_)
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMMDD') THEN 1 ELSE 0 END) AS MONTH_LOT_CNT,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMMDD') THEN GR_QTY ELSE 0 END) AS MONTH_GR_QTY,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMMDD') THEN SAM_CNT ELSE 0 END) AS MONTH_INSP_QTY,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMMDD') THEN REJECT_CNT ELSE 0 END) AS MONTH_INSP_DEF_QTY,
        SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 month', 'YYYYMMDD') AND UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS MONTH_FAIL_LOT,
        -- 어제 하루 (DAY_)
        SUM(CASE WHEN gr_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD') THEN 1 ELSE 0 END) AS DAY_LOT_CNT,
        SUM(CASE WHEN gr_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD') THEN GR_QTY ELSE 0 END) AS DAY_GR_QTY,
        SUM(CASE WHEN gr_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD') THEN SAM_CNT ELSE 0 END) AS DAY_INSP_QTY,
        SUM(CASE WHEN gr_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD') THEN REJECT_CNT ELSE 0 END) AS DAY_INSP_DEF_QTY,
        SUM(CASE WHEN gr_date = TO_CHAR(CURRENT_DATE - INTERVAL '1 day', 'YYYYMMDD') AND UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS DAY_FAIL_LOT
    FROM RawData
    GROUP BY PLANT, pjt_nm, CATEGORY_L, CATEGORY_S, SEC_CODE, MAKER_CD
),
MasterList AS (
    -- 3. 마스터 정보 (상세 함수 호출 최소화)
    SELECT 
        PLANT, pjt_nm, CATEGORY_L, CATEGORY_S, SEC_CODE, MAKER_CD
    FROM RawData
    GROUP BY PLANT, pjt_nm, CATEGORY_L, CATEGORY_S, SEC_CODE, MAKER_CD
)
-- 4. 최종 결과 조인 및 함수 적용
select
	TAPSUSER.FN_QCODE_NAME2(M.PLANT,'ko') AS PLANT,
	M.pjt_nm,
	M.CATEGORY_L, M.CATEGORY_S,
	M.SEC_CODE, M.MAKER_CD,
    -- 통계 데이터 매칭
    S.TOTAL_LOT_CNT, S.TOTAL_GR_QTY, S.TOTAL_INSP_QTY, S.TOTAL_INSP_DEF_QTY, S.TOTAL_FAIL_LOT,
    COALESCE(CAST(S.TOTAL_INSP_DEF_QTY * 1000000.0 / NULLIF(S.TOTAL_INSP_QTY, 0) AS INT), 0) AS TOTAL_INSP_PPM,
    COALESCE(ROUND(S.TOTAL_FAIL_LOT * 100.0 / NULLIF(S.TOTAL_LOT_CNT, 0), 2), 0) AS TOTAL_FAIL_RATE,
    S.MONTH_LOT_CNT, S.MONTH_GR_QTY, S.MONTH_INSP_QTY, S.MONTH_INSP_DEF_QTY, S.MONTH_FAIL_LOT,
    COALESCE(CAST(S.MONTH_INSP_DEF_QTY * 1000000.0 / NULLIF(S.MONTH_INSP_QTY, 0) AS INT), 0) AS MONTH_INSP_PPM,
    COALESCE(ROUND(S.MONTH_FAIL_LOT * 100.0 / NULLIF(S.MONTH_LOT_CNT, 0), 2), 0) AS MONTH_FAIL_RATE,
    S.WEEK_LOT_CNT, S.WEEK_GR_QTY, S.WEEK_INSP_QTY, S.WEEK_INSP_DEF_QTY, S.WEEK_FAIL_LOT,
    COALESCE(CAST(S.WEEK_INSP_DEF_QTY * 1000000.0 / NULLIF(S.WEEK_INSP_QTY, 0) AS INT), 0) AS WEEK_INSP_PPM,
    COALESCE(ROUND(S.WEEK_FAIL_LOT * 100.0 / NULLIF(S.WEEK_LOT_CNT, 0), 2), 0) AS WEEK_FAIL_RATE,
    S.DAY_LOT_CNT, S.DAY_GR_QTY, S.DAY_INSP_QTY, S.DAY_INSP_DEF_QTY, S.DAY_FAIL_LOT,
    COALESCE(CAST(S.DAY_INSP_DEF_QTY * 1000000.0 / NULLIF(S.DAY_INSP_QTY, 0) AS INT), 0) AS DAY_INSP_PPM,
    COALESCE(ROUND(S.DAY_FAIL_LOT * 100.0 / NULLIF(S.DAY_LOT_CNT, 0), 2), 0) AS DAY_FAIL_RATE
FROM MasterList M
LEFT JOIN Summary S 
       ON M.PLANT = S.PLANT
      and M.pjt_nm = S.pjt_nm 
      AND M.CATEGORY_L = S.CATEGORY_L
      AND M.CATEGORY_S = S.CATEGORY_S
      AND M.SEC_CODE = S.SEC_CODE
       AND M.MAKER_CD = S.MAKER_CD
ORDER BY M.PLANT, M.CATEGORY_L, M.CATEGORY_S;
    """
    
    try:
        print("[Insp Data] 데이터 추출 시작...")
        df = pd.read_sql(query, engine)
        print(f"[Insp Data] 성공적으로 로드 완료: {len(df)} 행")
        return df
    except Exception as e:
        print(f"[Insp Data] 오류 발생: {e}")
        raise


def save_insp_data(output_path: Path = None) -> Path:
    """
    입고검사 원본 데이터를 parquet 파일로 저장
    
    Args:
        output_path: 출력 파일 경로 (None이면 기본 경로 사용)
    
    Returns:
        저장된 파일 경로
    """
    if output_path is None:
        output_path = Path(__file__).parent / "insp_data.parquet"
    
    # 데이터 추출
    df = get_insp_data()
    
    # 컬럼명 소문자로 변환
    df.columns = df.columns.str.lower()
    
    # parquet으로 저장
    df.to_parquet(output_path, index=False)
    print(f"[Insp Data] 저장 완료: {output_path}")
    
    return output_path


# 프로세스 내 캐시: {파일경로: (mtime, DataFrame)} — 파일이 갱신되면 자동 재로드
_insp_data_cache: dict = {}


def load_insp_data(file_path: Path = None) -> pd.DataFrame:
    """
    저장된 입고검사 원본 데이터 로드 (파일 mtime 기준 캐시)

    Args:
        file_path: 파일 경로 (None이면 기본 경로 사용)

    Returns:
        DataFrame: 입고검사 원본 데이터 (호출자가 수정하면 안 됨. 필터/그룹핑 결과만 사용)
    """
    if file_path is None:
        file_path = Path(__file__).parent / "insp_data.parquet"

    if not file_path.exists():
        # 워커 여러 개가 동시에 생성하지 않도록 파일 락. 먼저 잡은 워커만 DB 조회, 나머지는 대기 후 재확인
        import fcntl
        lock_path = file_path.with_suffix(".lock")
        with open(lock_path, "w") as lock_f:
            fcntl.flock(lock_f, fcntl.LOCK_EX)
            if not file_path.exists():
                print(f"[Insp Data] 파일이 없음: {file_path}")
                save_insp_data(file_path)
            fcntl.flock(lock_f, fcntl.LOCK_UN)

    mtime = file_path.stat().st_mtime
    cached = _insp_data_cache.get(str(file_path))
    if cached is not None and cached[0] == mtime:
        return cached[1]

    df = pd.read_parquet(file_path)
    _insp_data_cache[str(file_path)] = (mtime, df)
    print(f"[Insp Data] 로드 완료: {len(df)} 행")

    return df


def group_by_plant(df: pd.DataFrame) -> pd.DataFrame:
    """
    원본 데이터를 PLANT별로 그룹핑
    
    Args:
        df: 원본 DataFrame
    
    Returns:
        DataFrame: PLANT별 집계 데이터
    """
    # PLANT별 합계 컬럼
    sum_columns = [
        'total_lot_cnt', 'total_gr_qty', 'total_insp_qty', 'total_insp_def_qty', 'total_fail_lot',
        'month_lot_cnt', 'month_gr_qty', 'month_insp_qty', 'month_insp_def_qty', 'month_fail_lot',
        'week_lot_cnt', 'week_gr_qty', 'week_insp_qty', 'week_insp_def_qty', 'week_fail_lot',
        'day_lot_cnt', 'day_gr_qty', 'day_insp_qty', 'day_insp_def_qty', 'day_fail_lot'
    ]
    
    # PLANT별 그룹핑
    grouped = df.groupby('plant').agg({
        **{col: 'sum' for col in sum_columns if col in df.columns}
    }).reset_index()
    
    # PPM 및 불량률 재계산
    # TOTAL
    grouped['total_insp_ppm'] = grouped.apply(
        lambda x: int((x['total_insp_def_qty'] * 1000000) / max(x['total_insp_qty'], 1)), axis=1
    )
    grouped['total_fail_rate'] = grouped.apply(
        lambda x: round((x['total_fail_lot'] * 100) / max(x['total_lot_cnt'], 1), 2), axis=1
    )
    
    # MONTH
    grouped['month_insp_ppm'] = grouped.apply(
        lambda x: int((x['month_insp_def_qty'] * 1000000) / max(x['month_insp_qty'], 1)), axis=1
    )
    grouped['month_fail_rate'] = grouped.apply(
        lambda x: round((x['month_fail_lot'] * 100) / max(x['month_lot_cnt'], 1), 2), axis=1
    )
    
    # WEEK
    grouped['week_insp_ppm'] = grouped.apply(
        lambda x: int((x['week_insp_def_qty'] * 1000000) / max(x['week_insp_qty'], 1)), axis=1
    )
    grouped['week_fail_rate'] = grouped.apply(
        lambda x: round((x['week_fail_lot'] * 100) / max(x['week_lot_cnt'], 1), 2), axis=1
    )
    
    # DAY
    grouped['day_insp_ppm'] = grouped.apply(
        lambda x: int((x['day_insp_def_qty'] * 1000000) / max(x['day_insp_qty'], 1)), axis=1
    )
    grouped['day_fail_rate'] = grouped.apply(
        lambda x: round((x['day_fail_lot'] * 100) / max(x['day_lot_cnt'], 1), 2), axis=1
    )
    
    return grouped


def group_by_category(df: pd.DataFrame) -> pd.DataFrame:
    """
    원본 데이터를 CATEGORY_L별로 그룹핑
    
    Args:
        df: 원본 DataFrame
    
    Returns:
        DataFrame: CATEGORY_L별 집계 데이터
    """
    # CATEGORY_L별 합계 컬럼
    sum_columns = [
        'total_lot_cnt', 'total_gr_qty', 'total_insp_qty', 'total_insp_def_qty', 'total_fail_lot',
        'month_lot_cnt', 'month_gr_qty', 'month_insp_qty', 'month_insp_def_qty', 'month_fail_lot',
        'week_lot_cnt', 'week_gr_qty', 'week_insp_qty', 'week_insp_def_qty', 'week_fail_lot',
        'day_lot_cnt', 'day_gr_qty', 'day_insp_qty', 'day_insp_def_qty', 'day_fail_lot'
    ]
    
    # CATEGORY_L별 그룹핑
    grouped = df.groupby('category_l').agg({
        **{col: 'sum' for col in sum_columns if col in df.columns}
    }).reset_index()
    
    grouped = grouped.rename(columns={'category_l': 'category'})
    
    # PPM 및 불량률 재계산
    # TOTAL
    grouped['total_insp_ppm'] = grouped.apply(
        lambda x: int((x['total_insp_def_qty'] * 1000000) / max(x['total_insp_qty'], 1)), axis=1
    )
    grouped['total_fail_rate'] = grouped.apply(
        lambda x: round((x['total_fail_lot'] * 100) / max(x['total_lot_cnt'], 1), 2), axis=1
    )
    
    # MONTH
    grouped['month_insp_ppm'] = grouped.apply(
        lambda x: int((x['month_insp_def_qty'] * 1000000) / max(x['month_insp_qty'], 1)), axis=1
    )
    grouped['month_fail_rate'] = grouped.apply(
        lambda x: round((x['month_fail_lot'] * 100) / max(x['month_lot_cnt'], 1), 2), axis=1
    )
    
    # WEEK
    grouped['week_insp_ppm'] = grouped.apply(
        lambda x: int((x['week_insp_def_qty'] * 1000000) / max(x['week_insp_qty'], 1)), axis=1
    )
    grouped['week_fail_rate'] = grouped.apply(
        lambda x: round((x['week_fail_lot'] * 100) / max(x['week_lot_cnt'], 1), 2), axis=1
    )
    
    # DAY
    grouped['day_insp_ppm'] = grouped.apply(
        lambda x: int((x['day_insp_def_qty'] * 1000000) / max(x['day_insp_qty'], 1)), axis=1
    )
    grouped['day_fail_rate'] = grouped.apply(
        lambda x: round((x['day_fail_lot'] * 100) / max(x['day_lot_cnt'], 1), 2), axis=1
    )
    
    # 정렬 (총 LOT 수 기준 내림차순)
    grouped = grouped.sort_values('total_lot_cnt', ascending=False).reset_index(drop=True)
    
    return grouped


# ===== 기존 호환성 유지를 위한 함수들 =====

def get_plant_defect_data() -> pd.DataFrame:
    """기존 함수 호환용 - get_insp_data() 호출"""
    return get_insp_data()


def save_plant_defect_data(output_path: Path = None) -> Path:
    """기존 함수 호환용 - save_insp_data() 호출"""
    return save_insp_data(output_path)


def load_plant_defect_data(file_path: Path = None, do_group_by_plant: bool = True) -> pd.DataFrame:
    """
    기존 함수 호환용 - load_insp_data() 후 그룹핑
    
    Args:
        file_path: 파일 경로
        do_group_by_plant: True면 PLANT별 그룹핑
    """
    df = load_insp_data(file_path)
    
    if do_group_by_plant:
        df = group_by_plant(df)
        print(f"[Insp Data] PLANT별 그룹핑 완료: {len(df)} 행")
    
    return df


def load_category_defect_data(file_path: Path = None) -> pd.DataFrame:
    """
    기존 함수 호환용 - load_insp_data() 후 CATEGORY_L별 그룹핑
    """
    df = load_insp_data(file_path)
    df = group_by_category(df)
    print(f"[Insp Data] CATEGORY_L별 그룹핑 완료: {len(df)} 행")
    
    return df


# 단독 실행 시 테스트용
if __name__ == "__main__":
    # 원본 데이터 저장
    save_insp_data()
    
    # 원본 데이터 로드
    df = load_insp_data()
    print("\n=== 원본 데이터 ===")
    print(df.head())
    
    # PLANT별 그룹핑
    plant_df = group_by_plant(df)
    print("\n=== PLANT별 그룹핑 ===")
    print(plant_df.head())
    
    # CATEGORY_L별 그룹핑
    category_df = group_by_category(df)
    print("\n=== CATEGORY_L별 그룹핑 ===")
    print(category_df.head())