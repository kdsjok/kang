import pandas as pd
import sys
import os
from datetime import datetime
from pathlib import Path

# DB 추출 함수 임포트 로직 보강
def get_db_function():
    try:
        from .mqis_db import get_db_data
        return get_db_data
    except (ImportError, ValueError):
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            if current_dir not in sys.path:
                sys.path.insert(0, current_dir)
            import mqis_db
            return mqis_db.get_db_data
        except Exception as e:
            print(f"[ERROR] Mqis_db.py 로드 실패: {e}")
            return None

def get_ace_data():
    """
    AB 이상감지 데이터 수집 (DB 직접 집계)
    - 기존 ACE 사이트 셀레늄 크롤링을 tbi_stat_vendor DB 집계로 대체
    - 반환: PART_CD, VEND_L_CD, AB_CNT, CPK_AB, EDGE_AB
    """
    try:
        # 현재 파일 위치 기준 절대 경로 설정
        current_dir = os.path.dirname(os.path.abspath(__file__))

        # sys.path에 현재 디렉토리가 없으면 추가
        if current_dir not in sys.path:
            sys.path.insert(0, current_dir)

        try:
            from ab_anomaly import get_ab_data
        except ImportError:
            from .ab_anomaly import get_ab_data

        print("[Step 2] AB 이상감지 데이터 수집 중 (tbi_stat_vendor)...")

        ab_df = get_ab_data()

        # 데이터가 비어있거나 에러가 났을 경우 처리
        if ab_df is None or ab_df.empty:
            return None
        return ab_df
    except Exception as e:
        print(f"[ERROR] AB 이상감지 데이터 수집 실패: {e}")
        import traceback
        traceback.print_exc()
        return None

def merge_process(db_df, ace_df):
    """두 DataFrame을 병합하여 최종 결과 반환"""
    print("[Step 3] 데이터 병합 및 가공 중...")
    
    # ACE/AB 데이터가 없을 경우 DB 데이터만이라도 구조를 맞춰서 반환
    if ace_df is None or ace_df.empty:
        print("[WARN] AB 데이터가 없어 DB 데이터 구조만 유지하여 반환합니다.")
        for col in ['AB_CNT', 'CPK_AB', 'EDGE_AB']:
            db_df[col] = None
        return db_df

    # 신규 경로: DB 집계(ab_anomaly) 결과는 이미 PART_CD × VEND_L_CD 키 보유
    if 'VEND_L_CD' in ace_df.columns:
        ab_df = ace_df[['PART_CD', 'VEND_L_CD', 'AB_CNT', 'CPK_AB', 'EDGE_AB']].copy()
        ab_df['PART_CD'] = ab_df['PART_CD'].astype(str).str.strip()
        ab_df['VEND_L_CD'] = ab_df['VEND_L_CD'].astype(str).str.strip()

        db_df = db_df.copy()
        db_df['PART_CD'] = db_df['PART_CD'].astype(str).str.strip()
        db_df['VEND_L_CD'] = db_df['VEND_L_CD'].astype(str).str.strip()

        final_df = pd.merge(db_df, ab_df, on=['PART_CD', 'VEND_L_CD'], how='left')
        matched = final_df['AB_CNT'].notna().sum()
        print(f"[Step 3] AB 데이터 병합 완료: 매칭 {matched}건 / 전체 {len(final_df)}건")
        return final_df

    # 기존 경로: 크롤링 데이터(HQ_CD 기반) 병합 (하위 호환)
    vendor_unique = db_df.drop_duplicates(subset=['MAKER_CD'], keep='first')
    
    ace_with_vend = pd.merge(
        ace_df, 
        vendor_unique[['MAKER_CD', 'VEND_L_CD', 'VEND_NM']], 
        left_on='HQ_CD', 
        right_on='MAKER_CD', 
        how='left'
    )
    
    ace_with_vend['VEND_L_CD'] = ace_with_vend['VEND_L_CD'].fillna(ace_with_vend['HQ_CD'])
    ace_final_cols = ace_with_vend[['PART_CD', 'VEND_L_CD', 'AB_CNT', 'CPK_AB', 'EDGE_AB']].copy()
    
    final_df = pd.merge(
        db_df,
        ace_final_cols,
        on=['PART_CD', 'VEND_L_CD'],
        how='left'
    )
    
    return final_df

def run_integration_process():
    """전체 프로세스를 실행하고 최종 데이터프레임을 반환"""
    start_time = datetime.now()
    print(f"[START] 프로세스 시작: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # 1. DB 데이터 가져오기
    print("[Step 1] DB 데이터 수집 중...")
    get_db_data = get_db_function()
    if get_db_data is None:
        print("[ERROR] DB 함수 로드 실패")
        return None
        
    db_df = get_db_data()
    if db_df is None or db_df.empty:
        print("[ERROR] DB 데이터 추출 결과가 없습니다.")
        return None
    print(f"[Step 1] DB 데이터 수집 완료: {len(db_df)}건")
    
    # 2. ACE 데이터 가져오기
    print("[Step 2] ACE 데이터 수집 중...")
    ace_df = get_ace_data()
    if ace_df is not None:
        print(f"[Step 2] ACE 데이터 수집 완료: {len(ace_df)}건")
    else:
        print("[Step 2] ACE 데이터 수집 실패 또는 데이터 없음")
    
    # 3. 병합 (ACE 데이터가 None이어도 merge_process 내부에서 처리됨)
    print("[Step 3] 데이터 병합 중...")
    final_result_df = merge_process(db_df, ace_df)
    
    if final_result_df is not None:
        # 전처리 모듈에서 기대하는 대문자 변환
        final_result_df.columns = final_result_df.columns.str.upper()
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        print(f"[DONE] 모든 처리 완료: {len(final_result_df)}건 (소요시간: {duration:.1f}초)")
        
    return final_result_df

if __name__ == "__main__":
    import argparse
    
    # 명령행 인자 파싱
    parser = argparse.ArgumentParser(description='입고검사 데이터 통합 처리')
    parser.add_argument('--output', type=str, default=None, 
                        help='결과를 저장할 parquet 파일 경로')
    args = parser.parse_args()
    
    # 데이터 처리 실행
    df = run_integration_process()
    
    if df is not None:
        print("\n--- 최종 데이터 샘플 ---")
        print(df.head())
        
        # 출력 파일이 지정된 경우 저장
        if args.output:
            df.to_parquet(args.output, index=False)
            print(f"\n[DONE] 결과 저장 완료: {args.output}")
    else:
        print("[ERROR] 데이터 처리 실패")