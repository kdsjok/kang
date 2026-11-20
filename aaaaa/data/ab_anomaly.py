"""
AB 이상감지 집계 모듈 (ACE 크롤링 대체)
- iqc_db의 tbi_stat_vendor(vw_tbi_stat_vendor_risk)에서 이상감지 이력을 직접 집계
- 판정 기준: effective_risk <> '정상' (예외 규칙 반영, 뷰 없으면 raw risk fallback)
- 윈도우: 최근 AB_CNT_WINDOW_DAYS일 (기본 14일) 내 이상 LOT 수
- 조기 해제: 마지막 이상감지 이후 연속 AB_CLEAR_NORMAL_LOTS개(기본 3) LOT이
  정상이면 AB_CNT=0 (감지 로직의 3LOT 연속 패턴과 대칭)
- 미제공 구분: 윈도우(14일) 내 제출 이력이 있는 PART×VENDOR만 결과에 포함
  → 병합 시 없는 부품은 NaN = 최근 데이터 입수이력 없음
  → 1등급 제외 + 무검사유지 혜택 불가 (최신 데이터 제공 시에만 혜택)
- 벤더 매핑: hq_cd → tbi_vendor_info.vendor_s_cd → vendor_g_cd,
  미매칭 시 hq_cd를 그대로 VEND_L_CD로 사용
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# 기본 파라미터 (config 로드 실패 시 fallback)
DEFAULT_WINDOW_DAYS = 14
DEFAULT_CLEAR_NORMAL_LOTS = 3

NORMAL_RISK = '정상'


def _load_params():
    """config.settings에서 파라미터 로드 (실패 시 기본값)"""
    try:
        root = str(Path(__file__).resolve().parents[2])
        if root not in sys.path:
            sys.path.insert(0, root)
        from I_ACE.config.settings import get_settings
        s = get_settings()
        return (
            int(getattr(s, 'AB_CNT_WINDOW_DAYS', DEFAULT_WINDOW_DAYS)),
            int(getattr(s, 'AB_CLEAR_NORMAL_LOTS', DEFAULT_CLEAR_NORMAL_LOTS)),
        )
    except Exception:
        return (DEFAULT_WINDOW_DAYS, DEFAULT_CLEAR_NORMAL_LOTS)


def _get_engine():
    """Read from the database selected by APP_ENV."""
    from database.db import get_engine
    return get_engine()


def fetch_stat_vendor_rows(engine, lookback_days: int) -> pd.DataFrame:
    """
    최근 lookback_days일 이상감지 이력 조회
    - 1순위: vw_tbi_stat_vendor_risk (effective_risk, 예외 규칙 반영)
    - fallback: tbi_stat_vendor (raw risk)
    """
    cutoff = (datetime.now() - timedelta(days=lookback_days)).strftime('%Y%m%d')

    view_sql = (
        "SELECT v.id, v.part_cd, v.hq_cd, v.ved_lot_no, v.in_dt, "
        "COALESCE(BTRIM(v.effective_risk), '') AS risk "
        "FROM vw_tbi_stat_vendor_risk v "
        "WHERE v.in_dt >= %(cutoff)s"
    )
    table_sql = (
        "SELECT v.id, v.part_cd, v.hq_cd, v.ved_lot_no, v.in_dt, "
        "COALESCE(BTRIM(v.risk), '') AS risk "
        "FROM tbi_stat_vendor v "
        "WHERE v.in_dt >= %(cutoff)s"
    )

    try:
        return pd.read_sql(view_sql, engine, params={'cutoff': cutoff})
    except Exception as e:
        print(f"[WARN] vw_tbi_stat_vendor_risk 조회 실패, raw risk로 대체: {e}")
        return pd.read_sql(table_sql, engine, params={'cutoff': cutoff})


def fetch_vendor_mapping(engine) -> pd.DataFrame:
    """tbi_vendor_info에서 vendor_s_cd → vendor_g_cd 매핑 조회"""
    try:
        return pd.read_sql(
            "SELECT vendor_s_cd, vendor_g_cd FROM tbi_vendor_info", engine
        )
    except Exception as e:
        print(f"[WARN] tbi_vendor_info 조회 실패, hq_cd 직접 매핑 사용: {e}")
        return pd.DataFrame(columns=['vendor_s_cd', 'vendor_g_cd'])


def aggregate_ab(
    rows: pd.DataFrame,
    vendor_info: pd.DataFrame,
    today: datetime = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    clear_lots: int = DEFAULT_CLEAR_NORMAL_LOTS,
) -> pd.DataFrame:
    """
    이상감지 이력을 PART_CD × VEND_L_CD 단위 AB_CNT/CPK_AB/EDGE_AB로 집계

    Args:
        rows: id, part_cd, hq_cd, ved_lot_no, in_dt(YYYYMMDD), risk
        vendor_info: vendor_s_cd, vendor_g_cd
        today: 기준일 (테스트용, 기본 현재)
        window_days: AB 카운트 윈도우 (일)
        clear_lots: 조기 해제에 필요한 연속 정상 LOT 수

    Returns:
        DataFrame: PART_CD, VEND_L_CD, AB_CNT, CPK_AB, EDGE_AB
        (윈도우 내 제출 이력이 있는 조합만 포함
         → 미포함 = 최근 데이터 입수이력 없음 = 결측 = 1등급 불가)
    """
    empty = pd.DataFrame(columns=['PART_CD', 'VEND_L_CD', 'AB_CNT', 'CPK_AB', 'EDGE_AB'])
    if rows is None or rows.empty:
        return empty

    today = today or datetime.now()
    window_cutoff = (today - timedelta(days=window_days)).strftime('%Y%m%d')

    df = rows.copy()
    df['part_cd'] = df['part_cd'].astype(str).str.strip()
    df['hq_cd'] = df['hq_cd'].astype(str).str.strip().str.upper()
    # in_dt는 DATE 또는 YYYYMMDD 문자열로 올 수 있다. 윈도우 기준값(YYYYMMDD)과 같은 형식으로 정규화한다.
    df['in_dt'] = pd.to_datetime(df['in_dt'], errors='coerce').dt.strftime('%Y%m%d')
    df['risk'] = df['risk'].fillna('').astype(str).str.strip()
    # 이상 = risk가 비어있지 않고 '정상'이 아닌 경우
    df['is_ab'] = (df['risk'] != '') & (df['risk'] != NORMAL_RISK)

    # LOT 단위 집계 (한 LOT에 측정항목별 여러 행이 있을 수 있음 → LOT 1건으로)
    lot = df.groupby(['part_cd', 'hq_cd', 'ved_lot_no'], dropna=False).agg(
        in_dt=('in_dt', 'max'),
        max_id=('id', 'max'),
        is_ab=('is_ab', 'any'),
        risks=('risk', lambda s: ' '.join(sorted(set(s.astype(str).str.upper())))),
    ).reset_index()

    results = []
    for (part, hq), grp in lot.groupby(['part_cd', 'hq_cd'], dropna=False):
        grp = grp.sort_values(['in_dt', 'max_id'], ascending=False)

        # 윈도우 내 제출 이력이 없으면 결과에서 제외
        # → 병합 시 NaN = 최근 데이터 입수이력 없음 = 1등급 불가
        win = grp[grp['in_dt'] >= window_cutoff]
        if win.empty:
            continue

        # 조기 해제 판정: 가장 최근 LOT부터 연속 정상 개수
        leading_normal = 0
        for is_ab in grp['is_ab']:
            if not is_ab:
                leading_normal += 1
            else:
                break

        # 윈도우 내 이상 LOT 집계
        ab_lots = win[win['is_ab']]
        ab_cnt = int(len(ab_lots))
        cpk_ab = int(ab_lots['risks'].str.contains('CPK', na=False).sum())
        edge_ab = int(ab_lots['risks'].str.contains('SPEC_EDGE', na=False).sum())

        # 조기 해제: 마지막 이상 이후 연속 clear_lots개 LOT 정상 → 0 처리
        if ab_cnt > 0 and leading_normal >= clear_lots:
            ab_cnt, cpk_ab, edge_ab = 0, 0, 0

        results.append({
            'part_cd': part,
            'hq_cd': hq,
            'AB_CNT': ab_cnt,
            'CPK_AB': cpk_ab,
            'EDGE_AB': edge_ab,
        })

    res = pd.DataFrame(results)
    if res.empty:
        return empty

    # 벤더 매핑: hq_cd → vendor_s_cd → vendor_g_cd, 미매칭 시 hq_cd 그대로
    if vendor_info is not None and not vendor_info.empty:
        vi = vendor_info.copy()
        vi['vendor_s_cd'] = vi['vendor_s_cd'].astype(str).str.strip().str.upper()
        vi['vendor_g_cd'] = vi['vendor_g_cd'].astype(str).str.strip()
        vi = vi.drop_duplicates(subset=['vendor_s_cd'], keep='first')
        res = res.merge(vi, left_on='hq_cd', right_on='vendor_s_cd', how='left')
        res['VEND_L_CD'] = res['vendor_g_cd'].fillna(res['hq_cd'])
    else:
        res['VEND_L_CD'] = res['hq_cd']

    res = res.rename(columns={'part_cd': 'PART_CD'})

    # 매핑 후 동일 PART×VEND_L_CD로 합쳐지는 경우 합산
    final = res.groupby(['PART_CD', 'VEND_L_CD'], as_index=False)[
        ['AB_CNT', 'CPK_AB', 'EDGE_AB']
    ].sum()

    return final


def get_ab_data() -> pd.DataFrame:
    """
    AB 이상감지 데이터 수집 (insp_data.py에서 호출하는 진입점)

    Returns:
        DataFrame: PART_CD, VEND_L_CD, AB_CNT, CPK_AB, EDGE_AB
        실패 시 None (호출측에서 결측 fallback 처리)
    """
    window_days, clear_lots = _load_params()

    print(f"[AB] tbi_stat_vendor 집계 시작 "
          f"(윈도우 {window_days}일, 조기해제 {clear_lots}LOT 연속 정상, "
          f"윈도우 내 미제출 = 결측 처리)")

    try:
        engine = _get_engine()
        rows = fetch_stat_vendor_rows(engine, window_days)
        vendor_info = fetch_vendor_mapping(engine)
    except Exception as e:
        print(f"[ERROR] AB 데이터 DB 조회 실패: {e}")
        return None

    if rows is None or rows.empty:
        print("[WARN] 이상감지 이력 조회 결과 없음")
        return None

    result = aggregate_ab(
        rows, vendor_info,
        window_days=window_days,
        clear_lots=clear_lots,
    )

    ab_parts = int((result['AB_CNT'] > 0).sum()) if not result.empty else 0
    print(f"[AB] 집계 완료: 최근 {window_days}일 제출이력 {len(result)}개 PART×VENDOR, "
          f"이상감지 보유 {ab_parts}개")

    return result


if __name__ == '__main__':
    df = get_ab_data()
    if df is not None:
        print(df.head(20))
        print(f"총 {len(df)}건")
