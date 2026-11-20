"""
CRUD 함수 정의
- DB 저장, 조회, 중복 체크
"""

import hashlib
import pandas as pd
from datetime import datetime
from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, and_, text
from database.batch_writer import dual_write, get_write_engine

from .models import QualityGrade


def generate_data_hash(row: dict) -> str:
    """
    데이터 해시 생성
    upload, id, data_hash 제외
    """
    exclude_keys = {"upload", "id", "data_hash"}

    hash_data = ""
    for key in sorted(row.keys()):
        if key not in exclude_keys:
            value = row.get(key)
            if value is not None:
                hash_data += f"{key}:{value}|"

    return hashlib.sha256(hash_data.encode()).hexdigest()


def check_duplicate(db: Session, data_hash: str) -> bool:
    """
    중복 데이터 체크
    """
    existing = db.query(QualityGrade).filter(
        QualityGrade.data_hash == data_hash
    ).first()

    return existing is not None


def save_analysis_result(
    db: Session,
    df: pd.DataFrame,
    upload_time: datetime = None
) -> Dict[str, int]:
    """
    분석 결과 DB 저장
    - id는 직접 넣지 않음
    - DB의 sequence/default로 자동 생성
    - bulk_save_objects 사용
    """
    if upload_time is None:
        upload_time = datetime.now()

    saved_count = 0
    skipped_count = 0

    column_mapping = {
        "PART_CD": "part_cd",
        "PART_NM": "part_nm",
        "MAKER_CD": "maker_cd",
        "VEND_NM": "vend_nm",
        "VEND_L_CD": "vend_l_cd",
        "CATEGORY_L": "category_l",
        "CATEGORY_S": "category_s",
        "TEST_LEVEL_CODE": "test_level_code",
        "TEN_WEEK_INSP_YN": "ten_week_insp_yn",
        "PART_Q_LEVEL": "rule_grade",
        "FINAL_GRADE": "final_grade",
        "HYBRID_GRADE": "hybrid_grade",
        "AI_SUGGESTED_GRADE": "ai_suggested_grade",
        "AI_CONFIDENCE": "ai_confidence",
        "LEVEL_REASON": "level_reason",
        "AI_REASONING": "ai_reasoning",
        "FINAL_REASON": "final_reason",
        "TOTAL_INSP_PPM": "total_insp_ppm",
        "TOTAL_FAIL_RATE": "total_fail_rate",
        "MONTH_INSP_PPM": "month_insp_ppm",
        "MONTH_FAIL_RATE": "month_fail_rate",
        "WEEK_INSP_PPM": "week_insp_ppm",
        "WEEK_FAIL_RATE": "week_fail_rate",
        "PRODC_PPM_WEEK": "prodc_ppm_week",
        "PRODC_PPM_MONTH": "prodc_ppm_month",
        "ppm_trend_ratio": "ppm_trend_ratio",
        "AB_CNT": "ab_cnt",
        "LEAVE_CNT": "leave_cnt",
        "TOTAL_LOT_CNT": "total_lot_cnt",
        "TOTAL_GR_QTY": "total_gr_qty",
        "TOTAL_INSP_QTY": "total_insp_qty",
        "TOTAL_INSP_DEF_QTY": "total_insp_def_qty",
        "MONTH_LOT_CNT": "month_lot_cnt",
        "MONTH_GR_QTY": "month_gr_qty",
        "MONTH_INSP_QTY": "month_insp_qty",
        "MONTH_INSP_DEF_QTY": "month_insp_def_qty",
        "WEEK_LOT_CNT": "week_lot_cnt",
        "WEEK_GR_QTY": "week_gr_qty",
        "WEEK_INSP_QTY": "week_insp_qty",
        "WEEK_INSP_DEF_QTY": "week_insp_def_qty",
        "defect_qty_MONTH": "defect_qty_month",
        "ACRS_QTY_MONTH": "acrs_qty_month",
        "defect_qty_WEEK": "defect_qty_week",
        "ACRS_QTY_WEEK": "acrs_qty_week",
        "LEAVE_PROC": "leave_proc",
        "LEAVE_FAIL_NM": "leave_fail_nm",
        "IF_CD": "if_cd",
        "CPK_AB": "cpk_ab",
        "EDGE_AB": "edge_ab",
    }

    print(f"[DB] 데이터 해시 생성 중... (총 {len(df)}건)")

    all_hashes = []
    all_data = []

    for _, row in df.iterrows():
        data = {}

        for orig_col, db_col in column_mapping.items():
            if orig_col in row.index:
                value = row[orig_col]

                # Series가 아니고 null이 아닌 경우만 저장
                if isinstance(value, (pd.Series, list, dict)):
                    # 복합 타입은 스킵
                    continue
                if pd.notna(value):
                    data[db_col] = value

        data_hash = generate_data_hash(data)
        all_hashes.append(data_hash)
        all_data.append(data)

    if not all_hashes:
        print("[DB] 저장할 데이터가 없습니다.")
        return {
            "saved": 0,
            "skipped": 0,
            "total": 0
        }

    print("[DB] 중복 체크 중...")

    db.execute(text("SELECT pg_advisory_xact_lock(73419023)"))
    existing_hashes = set(
        h[0]
        for h in db.query(QualityGrade.data_hash)
        .filter(QualityGrade.data_hash.in_(all_hashes))
        .all()
    )

    print(f"[DB] 기존 중복 데이터: {len(existing_hashes)}건")
    print("[DB] 데이터 저장 중...")

    batch_size = 1000
    records_to_add = []

    try:
        for data_hash, data in zip(all_hashes, all_data):
            if data_hash in existing_hashes:
                skipped_count += 1
                continue

            db_record = QualityGrade(
                upload=upload_time,
                data_hash=data_hash,
                **data
            )

            if data.get("level_reason", "").startswith("[ML High Risk]"):
                db_record.is_ml_high_risk = 1

            existing_hashes.add(data_hash)
            records_to_add.append(db_record)
            saved_count += 1

            if len(records_to_add) >= batch_size:
                db.bulk_save_objects(records_to_add)
                records_to_add = []
                print(f"[DB] 진행: {saved_count}/{len(df)} ({saved_count * 100 // len(df)}%)")

        if records_to_add:
            db.bulk_save_objects(records_to_add)
        db.commit()

    except Exception as e:
        db.rollback()
        print(f"[DB] 저장 실패: {e}")
        raise

    print(f"[DB] 전체 완료 - 저장: {saved_count}건, 중복 스킵: {skipped_count}건")

    return {
        "saved": saved_count,
        "skipped": skipped_count,
        "total": saved_count + skipped_count
    }


def get_recent_grade_history(
    db: Session,
    lookback_uploads: int = 10
) -> pd.DataFrame:
    """
    최근 N개 업로드의 등급 이력 조회 (래칫 판정용)

    Returns:
        DataFrame: part_cd, vend_l_cd, upload, final_grade, hybrid_grade
        (upload 내림차순 정렬)
    """
    recent_uploads = [
        r[0]
        for r in db.query(QualityGrade.upload)
        .distinct()
        .order_by(desc(QualityGrade.upload))
        .limit(lookback_uploads)
        .all()
    ]

    if not recent_uploads:
        return pd.DataFrame(
            columns=["part_cd", "vend_l_cd", "upload", "final_grade", "hybrid_grade"]
        )

    rows = db.query(
        QualityGrade.part_cd,
        QualityGrade.vend_l_cd,
        QualityGrade.upload,
        QualityGrade.final_grade,
        QualityGrade.hybrid_grade,
    ).filter(
        QualityGrade.upload.in_(recent_uploads)
    ).all()

    df = pd.DataFrame(
        rows,
        columns=["part_cd", "vend_l_cd", "upload", "final_grade", "hybrid_grade"]
    )

    return df.sort_values("upload", ascending=False).reset_index(drop=True)


def get_latest_results(
    db: Session,
    grade: Optional[int] = None,
    category: Optional[str] = None,
    ml_high_risk_only: bool = False
) -> List[QualityGrade]:
    """
    최신 업로드 결과 조회
    """
    latest_upload = db.query(func.max(QualityGrade.upload)).scalar()

    if latest_upload is None:
        return []

    query = db.query(QualityGrade).filter(
        QualityGrade.upload == latest_upload
    )

    if grade is not None:
        query = query.filter(QualityGrade.final_grade == grade)

    if category:
        query = query.filter(QualityGrade.category_l == category)

    if ml_high_risk_only:
        query = query.filter(QualityGrade.is_ml_high_risk == 1)

    return query.all()


def get_latest_upload_time(db: Session) -> Optional[datetime]:
    """
    최신 업로드 시간 조회
    """
    return db.query(func.max(QualityGrade.upload)).scalar()


def get_all_upload_dates(db: Session) -> List[datetime]:
    """
    모든 업로드 날짜 조회
    """
    results = db.query(QualityGrade.upload).distinct().order_by(
        desc(QualityGrade.upload)
    ).all()

    return [r[0] for r in results]


def get_results_by_date(
    db: Session,
    upload_date: datetime
) -> List[QualityGrade]:
    """
    특정 날짜 결과 조회
    """
    start_date = upload_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = upload_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    return db.query(QualityGrade).filter(
        QualityGrade.upload >= start_date,
        QualityGrade.upload <= end_date
    ).all()


def get_grade_summary(
    db: Session,
    upload_time: datetime = None
) -> Dict[str, int]:
    """
    등급 분포 요약 조회
    """
    if upload_time is None:
        upload_time = get_latest_upload_time(db)

    if upload_time is None:
        return {}

    results = db.query(
        QualityGrade.final_grade,
        func.count(QualityGrade.id)
    ).filter(
        QualityGrade.upload == upload_time
    ).group_by(
        QualityGrade.final_grade
    ).all()

    summary = {
        "grade_1_count": 0,
        "grade_2_count": 0,
        "grade_3_count": 0,
        "grade_4_count": 0,
        "grade_5_count": 0,
        "total_count": 0,
        "ml_high_risk_count": 0
    }

    for grade, count in results:
        summary[f"grade_{grade}_count"] = count
        summary["total_count"] += count

    ml_count = db.query(QualityGrade).filter(
        and_(
            QualityGrade.upload == upload_time,
            QualityGrade.is_ml_high_risk == 1
        )
    ).count()

    summary["ml_high_risk_count"] = ml_count

    return summary


def get_latest_by_part_cd(db: Session) -> List[QualityGrade]:
    """
    각 부품코드별 최신 데이터 조회
    """
    subquery = db.query(
        QualityGrade.part_cd,
        func.max(QualityGrade.upload).label("max_upload")
    ).group_by(
        QualityGrade.part_cd
    ).subquery()

    results = db.query(QualityGrade).join(
        subquery,
        and_(
            QualityGrade.part_cd == subquery.c.part_cd,
            QualityGrade.upload == subquery.c.max_upload
        )
    ).all()

    return results


def get_latest_by_part_cd_with_filter(
    db: Session,
    grade: Optional[int] = None,
    category: Optional[str] = None
) -> List[QualityGrade]:
    """
    각 부품코드별 최신 데이터 조회 필터 버전
    """
    subquery = db.query(
        QualityGrade.part_cd,
        func.max(QualityGrade.upload).label("max_upload")
    ).group_by(
        QualityGrade.part_cd
    ).subquery()

    query = db.query(QualityGrade).join(
        subquery,
        and_(
            QualityGrade.part_cd == subquery.c.part_cd,
            QualityGrade.upload == subquery.c.max_upload
        )
    )

    if grade is not None:
        query = query.filter(QualityGrade.final_grade == grade)

    if category:
        query = query.filter(QualityGrade.category_l == category)

    return query.all()


def results_to_dataframe(results: List[QualityGrade]) -> pd.DataFrame:
    """
    DB 결과를 DataFrame으로 변환
    """
    if not results:
        return pd.DataFrame()

    data = []

    for r in results:
        data.append({
            "id": r.id,
            "upload": r.upload,
            "part_cd": r.part_cd,
            "part_nm": r.part_nm,
            "maker_cd": r.maker_cd,
            "vend_nm": r.vend_nm,
            "vend_l_cd": r.vend_l_cd,
            "category_l": r.category_l,
            "category_s": r.category_s,
            "test_level_code": r.test_level_code,
            "ten_week_insp_yn": r.ten_week_insp_yn,
            "rule_grade": r.rule_grade,
            "final_grade": r.final_grade,
            "ai_suggested_grade": r.ai_suggested_grade,
            "ai_confidence": r.ai_confidence,
            "level_reason": r.level_reason,
            "ai_reasoning": r.ai_reasoning,
            "final_reason": r.final_reason,
            "total_insp_ppm": r.total_insp_ppm,
            "total_fail_rate": r.total_fail_rate,
            "month_insp_ppm": r.month_insp_ppm,
            "month_fail_rate": r.month_fail_rate,
            "week_insp_ppm": r.week_insp_ppm,
            "week_fail_rate": r.week_fail_rate,
            "prodc_ppm_week": r.prodc_ppm_week,
            "prodc_ppm_month": r.prodc_ppm_month,
            "ppm_trend_ratio": r.ppm_trend_ratio,
            "ab_cnt": r.ab_cnt,
            "leave_cnt": r.leave_cnt,
            "is_ml_high_risk": bool(r.is_ml_high_risk),
        })

    return pd.DataFrame(data)

@dual_write(stream="quality_grade")
def save_analysis_result_to_databases(df: pd.DataFrame, upload_time: datetime):
    """Persist the same analysis and timestamp; retry only unfinished targets."""
    with Session(bind=get_write_engine()) as db:
        return save_analysis_result(db, df, upload_time)
