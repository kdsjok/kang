import re
import pandas as pd
from sqlalchemy import create_engine

_CODE_PATTERN = re.compile(r'^[A-Za-z0-9_-]+$')


def build_vendmap_values(vendor_mapping: pd.DataFrame) -> str:
    """
    제조사코드 → 협력사 대표코드 매핑을 TAPS 쿼리의 VendMap CTE용 VALUES 목록으로 만든다.
    - 대표코드가 자기 자신인 행은 제외 (COALESCE(V.VEND_L_CD, A.MAKER_CD)로 대체됨)
    - 쿼리 문자열에 직접 삽입되므로 영숫자/하이픈/밑줄 코드만 허용
    - 매핑이 없으면 조인에 걸리지 않는 더미 1행을 넣어 VALUES 구문을 유지
    """
    if vendor_mapping is None or vendor_mapping.empty:
        return "('__NONE__', '__NONE__')"
    vm = vendor_mapping[['maker_cd', 'vend_l_cd']].dropna().astype(str)
    vm = vm[vm['maker_cd'] != vm['vend_l_cd']]
    vm = vm[vm['maker_cd'].str.match(_CODE_PATTERN) & vm['vend_l_cd'].str.match(_CODE_PATTERN)]
    vm = vm.drop_duplicates(subset=['maker_cd'], keep='first')
    if vm.empty:
        return "('__NONE__', '__NONE__')"
    return ",\n            ".join(f"('{a}', '{b}')" for a, b in zip(vm['maker_cd'], vm['vend_l_cd']))


def get_db_data():
    """DB에서 데이터를 추출하여 DataFrame으로 반환"""
    from database.db import get_engine
    from data.taps_config import get_taps_connection_url
    taps_engine = create_engine(get_taps_connection_url())
    iqc_engine = get_engine()

    # 4. IQC_DB에서 벤더 매핑 데이터 가져오기
    print("벤더 매핑 데이터 로드 중...")
    vendor_query = """
    SELECT DISTINCT vendor_s_cd as maker_cd, vendor_g_cd as vend_l_cd FROM tbi_vendor_info
    UNION ALL
    SELECT DISTINCT vendor_g_cd as maker_cd, vendor_g_cd as vend_l_cd FROM tbi_vendor_info
    """
    vendor_mapping = pd.read_sql(vendor_query, iqc_engine)
    # 중복 제거 (vendor_s_cd와 vendor_g_cd가 같은 경우)
    vendor_mapping = vendor_mapping.drop_duplicates(subset=['maker_cd'], keep='first')

    # 5. TAPS에서 기본 데이터 추출
    # 협력사 대표코드(VEND_L_CD) 매핑을 VendMap CTE로 주입해 RawData 단계에서 붙이고,
    # 입고검사 실적(Summary)은 부품 × 협력사(VEND_L_CD) 단위로 집계한다.
    # 행은 제조사(MAKER_CD)별로 유지되며, 같은 협력사의 제조사 행은 동일한 집계값을 갖는다.
    taps_query = """
    with VendMap(MAKER_CD, VEND_L_CD) AS (
        VALUES __VENDMAP_VALUES__
    ),
    RawData AS (
        SELECT
            A.PLANT, A.MAKER_CD, COALESCE(V.VEND_L_CD, A.MAKER_CD) AS VEND_L_CD,
            A.SEC_CODE, A.ITEM_GRP_CODE,
            A.UD_CD, A.INSP_STATUS, A.GR_DATE,
            A.GR_QTY, A.SAM_CNT, A.REJECT_CNT
        FROM TAPSUSER.T_SQCI_IPGO_INSP A
        LEFT JOIN VendMap V ON V.MAKER_CD = A.MAKER_CD
        WHERE A.gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '1 year', 'YYYYMMDD')
          AND A.INSP_STATUS IS NOT NULL
          AND TAPSUSER.FN_GET_PLANT_GBN(A.PLANT) = UPPER('TELE')
          and A.MAKER_CD NOT IN ('C5H2-A1', 'C5H2', 'C5H0-A7', 'C100-D1', 'C100-D2', 'C100-D4', 'C100-DR', 'C100-DS', 'C100-CA', 'C610-P1', 'C610-P2',
          'C610-P3', 'C430', 'C310', 'C310-P2', 'C540', 'C540-P2', 'C430-P2', 'C201-P1', 'C290-P1', 'C6A0', 'C6A0-P1',
          'C610', 'C820-A2', 'C670-A1', 'C570-A1', 'C6F0-A1', 'L6F0-A1', 'B100-A1', 'B3DX', 'L6F0-A3',
          'C1M0-A2', 'D3V5', 'L6E0-A1', 'C5H0-A1', 'C5H0-A8', 'C820', 'C820-A1', 'C820-00', 'C3HA-A2', 'C100',
          'C100-AZ', 'c100', 'C100-DD', 'C100-DR', 'C712-A1', 'C751-A1', 'C310-A6', 'C820-A3', 'C550-A2', 'C6K0-A2', 'BLWX', 'C100-LB', 'C310-S2', 'C560-A1')
          and SUBSTR(A.SEC_CODE,1,2) <> ('BA')
          AND SUBSTR(A.SEC_CODE,1,4) <> ('GH83')
    ),
    Summary AS (
        SELECT
            SEC_CODE, VEND_L_CD,
            COUNT(*) AS TOTAL_LOT_CNT,
            SUM(GR_QTY) AS TOTAL_GR_QTY,
            SUM(SAM_CNT) AS TOTAL_INSP_QTY,
            SUM(REJECT_CNT) AS TOTAL_INSP_DEF_QTY,
            SUM(CASE WHEN UD_CD = 'MQ0949' AND INSP_STATUS = 'MQ1102' THEN 1 ELSE 0 END) AS TOTAL_PASS_LOT,
            SUM(CASE WHEN UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS TOTAL_FAIL_LOT,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN 1 ELSE 0 END) AS WEEK_LOT_CNT,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN GR_QTY ELSE 0 END) AS WEEK_GR_QTY,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN SAM_CNT ELSE 0 END) AS WEEK_INSP_QTY,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN REJECT_CNT ELSE 0 END) AS WEEK_INSP_DEF_QTY,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') AND UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS WEEK_FAIL_LOT,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 weeks', 'YYYYMMDD')
                      AND gr_date <  TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN 1 ELSE 0 END) AS MONTH_LOT_CNT,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 weeks', 'YYYYMMDD')
                      AND gr_date <  TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN GR_QTY ELSE 0 END) AS MONTH_GR_QTY,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 weeks', 'YYYYMMDD')
                      AND gr_date <  TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN SAM_CNT ELSE 0 END) AS MONTH_INSP_QTY,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 weeks', 'YYYYMMDD')
                      AND gr_date <  TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') THEN REJECT_CNT ELSE 0 END) AS MONTH_INSP_DEF_QTY,
            SUM(CASE WHEN gr_date >= TO_CHAR(CURRENT_DATE - INTERVAL '6 weeks', 'YYYYMMDD')
                      AND gr_date <  TO_CHAR(CURRENT_DATE - INTERVAL '2 weeks', 'YYYYMMDD') AND UD_CD <> 'MQ0949' THEN 1 ELSE 0 END) AS MONTH_FAIL_LOT
        FROM RawData
        GROUP BY SEC_CODE, VEND_L_CD
    ),
    MasterList AS (
        SELECT
            SEC_CODE, MAKER_CD, VEND_L_CD,
            MAX(ITEM_GRP_CODE) AS ITEM_GRP_CODE
        FROM RawData
        GROUP BY SEC_CODE, MAKER_CD, VEND_L_CD
    )
    select
        TAPSUSER.FN_QCODE_NAME2(TAPSUSER.FN_GET_PARENT_CD(M.ITEM_GRP_CODE),'ko') AS CATEGORY_L,
        TAPSUSER.FN_QCODE_NAME2(M.ITEM_GRP_CODE, 'ko') AS CATEGORY_S,
        TAPSUSER.FN_GET_DETAIL_SPEC('TELE', M.SEC_CODE) AS PART_NM,
        M.SEC_CODE as PART_CD, M.MAKER_CD, M.VEND_L_CD,
        TAPSUSER.FN_GET_CORP_NM2(M.MAKER_CD, 'ko') AS VEND_NM,
        G.TEST_LEVEL_CODE,
        S.TOTAL_LOT_CNT, S.TOTAL_GR_QTY, S.TOTAL_INSP_QTY, S.TOTAL_INSP_DEF_QTY,
        COALESCE(CAST(S.TOTAL_INSP_DEF_QTY * 1000000.0 / NULLIF(S.TOTAL_INSP_QTY, 0) AS INT), 0) AS TOTAL_INSP_PPM,
        COALESCE(ROUND(S.TOTAL_FAIL_LOT * 100.0 / NULLIF(S.TOTAL_LOT_CNT, 0), 2), 0) AS TOTAL_FAIL_RATE,
        S.MONTH_LOT_CNT, S.MONTH_GR_QTY, S.MONTH_INSP_QTY, S.MONTH_INSP_DEF_QTY,
        COALESCE(CAST(S.MONTH_INSP_DEF_QTY * 1000000.0 / NULLIF(S.MONTH_INSP_QTY, 0) AS INT), 0) AS MONTH_INSP_PPM,
        COALESCE(ROUND(S.MONTH_FAIL_LOT * 100.0 / NULLIF(S.MONTH_LOT_CNT, 0), 2), 0) AS MONTH_FAIL_RATE,
        S.WEEK_LOT_CNT, S.WEEK_GR_QTY, S.WEEK_INSP_QTY, S.WEEK_INSP_DEF_QTY,
        COALESCE(CAST(S.WEEK_INSP_DEF_QTY * 1000000.0 / NULLIF(S.WEEK_INSP_QTY, 0) AS INT), 0) AS WEEK_INSP_PPM,
        COALESCE(ROUND(S.WEEK_FAIL_LOT * 100.0 / NULLIF(S.WEEK_LOT_CNT, 0), 2), 0) AS WEEK_FAIL_RATE,
        G.TEST_LEVEL_CODE
    FROM MasterList M
    LEFT JOIN Summary S ON M.SEC_CODE = S.SEC_CODE AND M.VEND_L_CD = S.VEND_L_CD
    LEFT JOIN (with base AS (
                SELECT DISTINCT ON (imp.part_code, cm.dealcorp_code)
                       imp.part_code,
                       imp.maker_code,
                       cm.dealcorp_code,
                       imp.approval_date,
                       imp.test_level_code
                FROM tapsuser.if_myproject_partappr imp
                LEFT JOIN (
                    SELECT
                        b.dealcorp_code,
                        a.maker_code
                    FROM tapsuser.T_CORP_MAP_MAIN a
                    JOIN tapsuser.T_CORP_MAP_MEMBER b
                        ON a.map_id = b.map_id
                    WHERE a.del_is = 'N'
                      AND b.del_is = 'N'
                ) cm
                    ON imp.maker_code = cm.maker_code
                WHERE imp.progress_status_code = 'PASS'
                ORDER BY
                    imp.part_code,
                    cm.dealcorp_code,
                    imp.approval_date DESC
            )
            SELECT DISTINCT ON (part_code, dealcorp_code)
                   part_code,
                   maker_code,
                   dealcorp_code,
                   approval_date,
                   test_level_code
            FROM base
            ORDER BY
                part_code,
                dealcorp_code,
                approval_date DESC
    ) G ON M.SEC_CODE = G.part_code
    ORDER BY M.MAKER_CD, M.SEC_CODE;
    """
    taps_query = taps_query.replace('__VENDMAP_VALUES__', build_vendmap_values(vendor_mapping))

    try:
        print("데이터 추출 시작 (DB)...")
        # TAPS에서 기본 데이터 추출
        print(f"TAPS 쿼리 실행 중... (쿼리 길이: {len(taps_query)})")
        df = pd.read_sql(taps_query, taps_engine)
        print(f"✓ TAPS 쿼리 완료: {len(df)}행, 컬럼: {df.columns.tolist()}")

        # 컬럼명을 대문자로 통일 (PostgreSQL은 기본값이 소문자)
        df.columns = df.columns.str.upper()

        # VEND_L_CD는 TAPS 쿼리의 VendMap CTE에서 이미 매핑됨 (미매핑은 MAKER_CD 그대로)
        df['VEND_L_CD'] = df['VEND_L_CD'].fillna(df['MAKER_CD'])

        # IQC_DB에서 ExcludeList 로드
        print("제외 패턴 데이터 로드 중...")
        exclude_query = """
        SELECT part_nm_pattern, part_cd_pattern FROM tbi_10w_target_exc WHERE use_yn = 'Y'
        """
        exclude_patterns = pd.read_sql(exclude_query, iqc_engine)

        # IncludeList (포함 항목) 로드
        include_query = """
        SELECT include_keyword FROM tbi_10w_include_kwd WHERE use_yn = 'Y'
        """
        try:
            include_keywords_df = pd.read_sql(include_query, iqc_engine)
            include_keywords = include_keywords_df['include_keyword'].tolist()
        except:
            # 테이블이 없으면 기본값 사용
            include_keywords = [
                'OLED', 'UB', 'FRONT', 'REAR', 'FINGERPRINT MODULE', 'FINGER PRINT',
                'ASSY US FP', 'SIDE KEY', 'POWER KEY', 'VOLUME KEY', 'KEY', 'SIM TRAY',
                'CASE METAL', 'HINGE HOUSING', 'FRONT GLASS', 'BACK GLASS', 'BACK COVER',
                'GLASS COVER', 'CAM DECO', 'DECO CAMERA', 'CAM GLASS DECO', 'CAM WINDOW',
                'SPEAKER MESH', 'DECORATION-CAM DECO', 'DECORATION-CAMERA', 'DECORATION-5G ANT',
                'P CAP', '5G DECO', 'WINDOW GLASS', 'WINDOW DISPLAY', 'STYLUS PEN', 'SPEN',
                'KEY PAIR', 'FLASH LENS', 'PROTECTOR FILM', 'WHEEL', 'STRAP RUBBER',
                'BUCKLE RING', 'BUCKLE TONGUE', 'HOME KEY', 'CENTER KEY', 'O-RING',
                'CHARGER', 'TWS RCV ASSY', 'CRADLE', 'DECO STEM', 'STAND', 'RUBBER EAR TIP',
                'RUBBER WING TIP', 'KEYMODULE', 'TOUCHPAD', 'HINGE', 'KEY MODULE'
            ]

        def assign_inspection_yn(row):
            try:
                test_level = row.get('TEST_LEVEL_CODE')
                if pd.isna(test_level):
                    return 'N'

                test_level = str(test_level).strip().upper()
                if test_level == 'A':
                    return 'Y'
                if test_level != 'B':
                    return 'N'

                part_nm = str(row.get('PART_NM') or '').upper()
                part_cd = str(row.get('PART_CD') or '').upper()

                # ExcludeList 확인
                for _, exc_row in exclude_patterns.iterrows():
                    pat_nm = str(exc_row['part_nm_pattern']).replace('%%', '').strip()
                    pat_cd = str(exc_row['part_cd_pattern']).replace('%%', '').strip()
                    if pat_nm and pat_cd:
                        try:
                            if pat_nm in part_nm and pat_cd in part_cd:
                                return 'N'
                        except (TypeError, ValueError):
                            continue

                # 포함 항목 확인
                for keyword in include_keywords:
                    try:
                        if keyword.upper() in part_nm:
                            return 'Y'
                    except (TypeError, ValueError):
                        continue

                return 'N'
            except Exception as e:
                return 'N'

        df['TEN_WEEK_INSP_YN'] = df.apply(assign_inspection_yn, axis=1)

        # 컬럼명을 대문자로 통일
        df.columns = df.columns.str.upper()

        print(f"성공적으로 로드 완료: {len(df)} 행")
        return df

    except Exception as e:
        print(f"오류 발생: {e}")
        return None

# 단독 실행 시 테스트용
if __name__ == "__main__":
    df = get_db_data()
    if df is not None:
        print(df.head())
