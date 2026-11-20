"""I-ACE grade query API; existing /api/v1/grade URLs are preserved."""
from datetime import datetime
from typing import Optional
import pandas as pd
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, text, func
from database.db import get_db
from I_ACE.persistence.models import QualityGrade

router = APIRouter(tags=["Quality Grade Analysis"])

def _safe_number(value) -> float:
    if pd.isna(value):
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


@router.get("/grade/latest")
def get_latest_grade(
    part_cd: str = Query(..., description="부품코드 (PART_CD)"),
    vend_l_cd: str = Query(..., description="협력사코드 (VEND_L_CD)"),
    db: Session = Depends(get_db)
):
    """
    부품코드와 협력사코드로 최신 등급 데이터 1건 조회 (전체 컬럼)
    """
    try:
        result = db.query(QualityGrade).filter(
            and_(
                QualityGrade.part_cd == part_cd,
                QualityGrade.vend_l_cd == vend_l_cd
            )
        ).order_by(
            desc(QualityGrade.upload)
        ).first()
        
        if not result:
            raise HTTPException(
                status_code=404, 
                detail=f"데이터를 찾을 수 없습니다. (part_cd: {part_cd}, vend_l_cd: {vend_l_cd})"
            )
        
        # 전체 데이터 반환
        data = {
            'id': result.id,
            'upload': _format_upload(result.upload),
            'part_cd': result.part_cd,
            'part_nm': result.part_nm,
            'maker_cd': result.maker_cd,
            'vend_nm': result.vend_nm,
            'vend_l_cd': result.vend_l_cd,
            'category_l': result.category_l,
            'category_s': result.category_s,
            'test_level_code': result.test_level_code,
            'ten_week_insp_yn': result.ten_week_insp_yn,
            'rule_grade': result.rule_grade,
            'hybrid_grade': result.hybrid_grade,
            'final_grade': result.final_grade,
            'ai_suggested_grade': result.ai_suggested_grade,
            'ai_confidence': result.ai_confidence,
            'level_reason': result.level_reason,
            'ai_reasoning': result.ai_reasoning,
            'final_reason': result.final_reason,
            'total_insp_ppm': result.total_insp_ppm,
            'total_fail_rate': result.total_fail_rate,
            'month_insp_ppm': result.month_insp_ppm,
            'month_fail_rate': result.month_fail_rate,
            'week_insp_ppm': result.week_insp_ppm,
            'week_fail_rate': result.week_fail_rate,
            'prodc_ppm_week': result.prodc_ppm_week,
            'prodc_ppm_month': result.prodc_ppm_month,
            'ppm_trend_ratio': result.ppm_trend_ratio,
            'ab_cnt': result.ab_cnt,
            'leave_cnt': result.leave_cnt,
            'total_lot_cnt': result.total_lot_cnt,
            'total_gr_qty': result.total_gr_qty,
            'total_insp_qty': result.total_insp_qty,
            'total_insp_def_qty': result.total_insp_def_qty,
            'month_lot_cnt': result.month_lot_cnt,
            'month_gr_qty': result.month_gr_qty,
            'month_insp_qty': result.month_insp_qty,
            'month_insp_def_qty': result.month_insp_def_qty,
            'week_lot_cnt': result.week_lot_cnt,
            'week_gr_qty': result.week_gr_qty,
            'week_insp_qty': result.week_insp_qty,
            'week_insp_def_qty': result.week_insp_def_qty,
            'defect_qty_month': result.defect_qty_month,
            'acrs_qty_month': result.acrs_qty_month,
            'defect_qty_week': result.defect_qty_week,
            'acrs_qty_week': result.acrs_qty_week,
            'leave_proc': result.leave_proc,
            'leave_fail_nm': result.leave_fail_nm,
            'if_cd': result.if_cd,
            'cpk_ab': result.cpk_ab,
            'edge_ab': result.edge_ab,
            'is_ml_high_risk': bool(result.is_ml_high_risk)
        }
        
        return {
            "success": True,
            "message": "최신 데이터 조회 완료",
            "data": data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조회 오류: {str(e)}")


def _format_upload(value) -> Optional[str]:
    """upload 값 직렬화 (DB 컬럼이 TEXT라 str/datetime 모두 올 수 있음)"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return str(value)


LATEST_GRADE_FIELDS = (
    'upload',
    'part_cd',
    'part_nm',
    'maker_cd',
    'vend_nm',
    'vend_l_cd',
    'category_l',
    'category_s',
    'final_grade',
    'level_reason',
    'total_insp_ppm',
    'total_fail_rate',
    'month_insp_ppm',
    'month_fail_rate',
    'week_insp_ppm',
    'week_fail_rate',
    'prodc_ppm_week',
    'prodc_ppm_month',
    'ppm_trend_ratio',
    'ab_cnt',
)


def _quality_grade_to_dict(result) -> dict:
    """Serialize only the projected snapshot columns, without ORM lazy loads."""
    data = {field: getattr(result, field) for field in LATEST_GRADE_FIELDS}
    data["upload"] = _format_upload(data["upload"])
    return data


@router.get("/grade/latest-by-part")
def get_latest_grade_by_part(
    date: str = Query(
        ...,
        pattern=r"^(?:[0-9]{8}|[0-9]{4}-[0-9]{2}-[0-9]{2})$",
        description="조회 기준일 (필수, YYYYMMDD 또는 YYYY-MM-DD, 해당 날짜까지 포함)",
    ),
    part_cd: Optional[str] = Query(None, description="부품코드 (PART_CD, 옵션 - 미지정 시 전체 부품)"),
    maker_cd: Optional[str] = Query(None, description="제조사코드 (MAKER_CD, 옵션 - 미지정 시 전체 제조사)"),
    db: Session = Depends(get_db)
):
    """
    조회 기준일까지의 부품·제조사별 최신 등급 데이터 조회 (지정된 20개 컬럼)
    - date 필수: upload 날짜가 조회 기준일 이하인 데이터만 조회
    - part_cd, maker_cd 선택: 생략한 조건은 전체 조회
    - (PART_CD, MAKER_CD) 조합별 가장 최근 upload 1건 반환
    - 같은 upload의 중복 데이터는 가장 큰 id 1건 반환
    """
    try:
        date_format = '%Y%m%d' if len(date) == 8 else '%Y-%m-%d'
        as_of_date = datetime.strptime(date, date_format).date().isoformat()
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="date는 유효한 날짜여야 합니다. (YYYYMMDD 또는 YYYY-MM-DD)",
        )

    try:
        # upload는 ISO 날짜·시간 문자열(TEXT). 날짜 부분으로 비교하여 당일 전체를 포함한다.
        filters = [
            QualityGrade.upload.isnot(None),
            QualityGrade.upload != '',
            func.substr(QualityGrade.upload, 1, 10) <= as_of_date,
        ]
        if part_cd:
            filters.append(QualityGrade.part_cd == part_cd)
        if maker_cd:
            filters.append(QualityGrade.maker_cd == maker_cd)

        # 날짜/코드 필터를 먼저 적용한 뒤 조합별 최신 행을 선택한다.
        ranked = db.query(
            QualityGrade.id.label('grade_id'),
            func.row_number().over(
                partition_by=(QualityGrade.part_cd, QualityGrade.maker_cd),
                order_by=(desc(QualityGrade.upload), desc(QualityGrade.id)),
            ).label('row_num'),
        ).filter(
            *filters
        ).subquery()

        rows = db.query(*(getattr(QualityGrade, field) for field in LATEST_GRADE_FIELDS)).join(
            ranked, QualityGrade.id == ranked.c.grade_id,
        ).filter(
            ranked.c.row_num == 1,
        ).order_by(
            desc(QualityGrade.upload), desc(QualityGrade.id)
        ).all()

        if not rows:
            conditions = [f"date: {as_of_date}"]
            if part_cd:
                conditions.append(f"part_cd: {part_cd}")
            if maker_cd:
                conditions.append(f"maker_cd: {maker_cd}")
            detail = "데이터를 찾을 수 없습니다. (" + ", ".join(conditions) + ")"
            raise HTTPException(status_code=404, detail=detail)

        return {
            "success": True,
            "message": "최신 데이터 조회 완료",
            "date": as_of_date,
            "part_cd": part_cd,
            "maker_cd": maker_cd,
            "count": len(rows),
            "data": [_quality_grade_to_dict(r) for r in rows]
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조회 오류: {str(e)}")


@router.get("/grade/dashboard")
def get_grade_dashboard(
    part_cd: str = Query(..., description="부품코드 (PART_CD)"),
    maker_cd: str = Query(..., description="제조사코드 (MAKER_CD)"),
    db: Session = Depends(get_db)
):
    """
    대시보드용 등급 데이터 조회
    
    - 주요 지표만 반환 (프론트엔드 대시보드용)
    - LLM 요약 메시지 포함
    - maker_cd 또는 vend_l_cd로 조회 (둘 다 시도)
    - upload가 NULL이면 id로 정렬
    """
    try:
        # 1차 시도: maker_cd로 검색 (id 내림차순으로 최신 데이터 조회)
        result = db.query(QualityGrade).filter(
            and_(
                QualityGrade.part_cd == part_cd,
                QualityGrade.maker_cd == maker_cd
            )
        ).order_by(
            desc(QualityGrade.id)
        ).first()
        
        # 2차 시도: maker_cd가 없으면 vend_l_cd로 검색
        if not result:
            result = db.query(QualityGrade).filter(
                and_(
                    QualityGrade.part_cd == part_cd,
                    QualityGrade.vend_l_cd == maker_cd
                )
            ).order_by(
                desc(QualityGrade.id)
            ).first()
        
        if not result:
            raise HTTPException(
                status_code=404, 
                detail=f"데이터를 찾을 수 없습니다. (part_cd: {part_cd}, maker_cd/vend_l_cd: {maker_cd})"
            )
        
        # 등급 한글명 매핑
        grade_names = {1: "최우수", 2: "우수", 3: "보통", 4: "주의", 5: "위험"}
        
        # LLM 요약 메시지 생성
        category_l = result.category_l if result.category_l else "미분류"
        grade_name = grade_names.get(result.final_grade, "미정")
        summary = f"해당 부품은 {category_l}(부품군)으로 {grade_name}({result.final_grade}등급) 품질등급을 부여하였습니다."
        
        # 대시보드용 데이터
        dashboard_data = {
            # 기본 정보
            "part_cd": result.part_cd,
            "part_nm": result.part_nm,
            "vend_nm": result.vend_nm,
            "category_l": result.category_l,
            "test_level_code": result.test_level_code,
            "ten_week_insp_yn": result.ten_week_insp_yn,
            "upload": result.upload if result.upload else None,
            
            # 핵심 지표
            "final_grade": result.final_grade,
            "grade_name": grade_names.get(result.final_grade, "미정"),
            
            # 품질 지표
            "total_insp_ppm": round(_safe_number(result.total_insp_ppm), 2),
            "month_insp_ppm": round(_safe_number(result.month_insp_ppm), 2),
            "week_insp_ppm": round(_safe_number(result.week_insp_ppm), 2),
            "prodc_ppm_month": round(_safe_number(result.prodc_ppm_month), 2),
            "prodc_ppm_week": round(_safe_number(result.prodc_ppm_week), 2),
            "ppm_trend_ratio": round(_safe_number(result.ppm_trend_ratio), 2),

            # PPM 산출 분자/분모
            "total_insp_qty": int(_safe_number(result.total_insp_qty)),
            "total_insp_def_qty": int(_safe_number(result.total_insp_def_qty)),
            "month_insp_qty": int(_safe_number(result.month_insp_qty)),
            "month_insp_def_qty": int(_safe_number(result.month_insp_def_qty)),
            "week_insp_qty": int(_safe_number(result.week_insp_qty)),
            "week_insp_def_qty": int(_safe_number(result.week_insp_def_qty)),
            "acrs_qty_month": int(_safe_number(result.acrs_qty_month)),
            "defect_qty_month": int(_safe_number(result.defect_qty_month)),
            "acrs_qty_week": int(_safe_number(result.acrs_qty_week)),
            "defect_qty_week": int(_safe_number(result.defect_qty_week)),
            
            # 이상 지표
            "ab_cnt": int(_safe_number(result.ab_cnt)),
            "leave_cnt": int(_safe_number(result.leave_cnt)),
            
            # 판정 사유
            "level_reason": result.level_reason,
            
            # LLM 요약
            "llm_summary": summary
        }
        
        return {
            "success": True,
            "message": "대시보드 데이터 조회 완료",
            "data": dashboard_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조회 오류: {str(e)}")


@router.get("/grade/status-summary")
def get_grade_status_summary(db: Session = Depends(get_db)):
    """
    품질 등급 현황 요약 조회

    - 부품코드(PART_CD)별 최신 데이터 1건 기준 등급 분포 산출
    - 등급 증감은 같은 부품코드의 최신 upload와 직전 upload를 비교
    """
    try:
        grade_names = {1: "최우수", 2: "우수", 3: "보통", 4: "주의", 5: "위험"}

        summary_sql = text("""
            WITH latest AS (
                SELECT DISTINCT ON (part_cd)
                    part_cd,
                    id,
                    upload,
                    final_grade
                FROM tbi_quality_grade
                WHERE part_cd IS NOT NULL
                  AND upload IS NOT NULL
                  AND final_grade BETWEEN 1 AND 5
                ORDER BY part_cd, upload DESC NULLS LAST, id DESC
            ),
            previous AS (
                SELECT DISTINCT ON (q.part_cd)
                    q.part_cd,
                    q.id,
                    q.upload,
                    q.final_grade
                FROM tbi_quality_grade q
                JOIN latest l
                  ON l.part_cd = q.part_cd
                WHERE q.part_cd IS NOT NULL
                  AND q.upload IS NOT NULL
                  AND q.final_grade BETWEEN 1 AND 5
                  AND q.upload < l.upload
                ORDER BY q.part_cd, q.upload DESC NULLS LAST, q.id DESC
            ),
            joined AS (
                SELECT
                    l.part_cd,
                    l.upload AS latest_upload,
                    l.final_grade AS latest_grade,
                    p.upload AS previous_upload,
                    p.final_grade AS previous_grade
                FROM latest l
                LEFT JOIN previous p
                  ON p.part_cd = l.part_cd
            )
            SELECT
                MAX(latest_upload) AS reference_upload,
                COUNT(*) AS total_count,
                COUNT(*) FILTER (WHERE latest_grade = 1) AS grade_1_count,
                COUNT(*) FILTER (WHERE latest_grade = 2) AS grade_2_count,
                COUNT(*) FILTER (WHERE latest_grade = 3) AS grade_3_count,
                COUNT(*) FILTER (WHERE latest_grade = 4) AS grade_4_count,
                COUNT(*) FILTER (WHERE latest_grade = 5) AS grade_5_count,
                COUNT(*) FILTER (WHERE previous_grade = 1) AS previous_grade_1_count,
                COUNT(*) FILTER (WHERE previous_grade = 2) AS previous_grade_2_count,
                COUNT(*) FILTER (WHERE previous_grade = 3) AS previous_grade_3_count,
                COUNT(*) FILTER (WHERE previous_grade = 4) AS previous_grade_4_count,
                COUNT(*) FILTER (WHERE previous_grade = 5) AS previous_grade_5_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND latest_grade = 1
                ) AS grade_1_in_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND latest_grade = 2
                ) AS grade_2_in_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND latest_grade = 3
                ) AS grade_3_in_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND latest_grade = 4
                ) AS grade_4_in_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND latest_grade = 5
                ) AS grade_5_in_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND previous_grade = 1
                ) AS grade_1_out_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND previous_grade = 2
                ) AS grade_2_out_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND previous_grade = 3
                ) AS grade_3_out_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND previous_grade = 4
                ) AS grade_4_out_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                      AND previous_grade = 5
                ) AS grade_5_out_count,
                COUNT(*) FILTER (WHERE previous_grade IS NULL) AS new_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade = previous_grade
                ) AS unchanged_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade <> previous_grade
                ) AS changed_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade > previous_grade
                ) AS increased_count,
                COUNT(*) FILTER (
                    WHERE previous_grade IS NOT NULL
                      AND latest_grade < previous_grade
                ) AS decreased_count
            FROM joined
        """)

        row = db.execute(summary_sql).mappings().first()

        if not row or not row["total_count"]:
            return {
                "success": True,
                "message": "품질 등급 현황 데이터가 없습니다.",
                "data": {
                    "reference_upload": None,
                    "total_count": 0,
                    "grades": [
                        {
                            "grade": grade,
                            "grade_name": grade_names[grade],
                            "count": 0,
                            "ratio": 0,
                            "previous_count": 0,
                            "delta_count": 0
                        }
                        for grade in range(1, 6)
                    ],
                    "changes": {
                        "changed_count": 0,
                        "increased_count": 0,
                        "decreased_count": 0,
                        "unchanged_count": 0,
                        "new_count": 0
                    }
                }
            }

        total_count = int(row["total_count"] or 0)
        grades = []

        for grade in range(1, 6):
            count = int(row[f"grade_{grade}_count"] or 0)
            previous_count = int(row[f"previous_grade_{grade}_count"] or 0)
            incoming_count = int(row[f"grade_{grade}_in_count"] or 0)
            outgoing_count = int(row[f"grade_{grade}_out_count"] or 0)
            grades.append({
                "grade": grade,
                "grade_name": grade_names[grade],
                "count": count,
                "ratio": round((count / total_count) * 100, 1) if total_count else 0,
                "previous_count": previous_count,
                "delta_count": incoming_count - outgoing_count
            })

        return {
            "success": True,
            "message": "품질 등급 현황 요약 조회 완료",
            "data": {
                "reference_upload": row["reference_upload"],
                "total_count": total_count,
                "grades": grades,
                "changes": {
                    "changed_count": int(row["changed_count"] or 0),
                    "increased_count": int(row["increased_count"] or 0),
                    "decreased_count": int(row["decreased_count"] or 0),
                    "unchanged_count": int(row["unchanged_count"] or 0),
                    "new_count": int(row["new_count"] or 0)
                }
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조회 오류: {str(e)}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조회 오류: {str(e)}")


@router.get("/grade/status-summary/{grade}/part-distribution")
def get_grade_part_distribution(
    grade: int,
    db: Session = Depends(get_db)
):
    """
    특정 등급의 부품종 분포 조회

    - 부품코드(PART_CD)별 최신 데이터 1건 기준
    - CATEGORY_L / CATEGORY_S별 부품코드 수량 집계
    """
    if grade < 1 or grade > 5:
        raise HTTPException(status_code=400, detail="grade는 1~5 사이여야 합니다.")

    try:
        grade_names = {1: "최우수", 2: "우수", 3: "보통", 4: "주의", 5: "위험"}

        distribution_sql = text("""
            WITH latest AS (
                SELECT DISTINCT ON (part_cd)
                    part_cd,
                    COALESCE(NULLIF(category_l, ''), '미분류') AS category_l,
                    COALESCE(NULLIF(category_s, ''), '미분류') AS category_s,
                    upload,
                    final_grade
                FROM tbi_quality_grade
                WHERE part_cd IS NOT NULL
                  AND upload IS NOT NULL
                  AND final_grade BETWEEN 1 AND 5
                ORDER BY part_cd, upload DESC NULLS LAST, id DESC
            ),
            filtered AS (
                SELECT *
                FROM latest
                WHERE final_grade = :grade
            ),
            grouped AS (
                SELECT
                    category_l,
                    category_s,
                    COUNT(*) AS count
                FROM filtered
                GROUP BY category_l, category_s
            ),
            total AS (
                SELECT COUNT(*) AS total_count
                FROM filtered
            )
            SELECT
                grouped.category_l,
                grouped.category_s,
                grouped.count,
                total.total_count
            FROM grouped
            CROSS JOIN total
            ORDER BY grouped.count DESC, grouped.category_l, grouped.category_s
        """)

        rows = db.execute(distribution_sql, {"grade": grade}).mappings().all()
        total_count = int(rows[0]["total_count"] or 0) if rows else 0

        return {
            "success": True,
            "message": f"{grade}등급 부품종 분포 조회 완료",
            "data": {
                "grade": grade,
                "grade_name": grade_names[grade],
                "total_count": total_count,
                "rows": [
                    {
                        "category_l": row["category_l"],
                        "category_s": row["category_s"],
                        "count": int(row["count"] or 0),
                        "ratio": round((int(row["count"] or 0) / total_count) * 100, 1) if total_count else 0
                    }
                    for row in rows
                ]
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"조회 오류: {str(e)}")



@router.get("/iace/instruments")
def get_measuring_instruments(db: Session = Depends(get_db)):
    """계측기 목록 조회 - tbi_measurung_instrument. 검사 카테고리(외관/치수/신뢰성 등) 확인용."""
    try:
        rows = db.execute(text("""
            SELECT seq, division, inst_cd, cat_item, inst_nm_kr, inst_nm_en, use_yn, reg_dt
            FROM tbi_measurung_instrument
            ORDER BY cat_item, inst_cd
        """)).mappings().all()
        items = [
            {
                "seq": row["seq"],
                "division": row["division"],
                "inst_cd": row["inst_cd"],
                "cat_item": row["cat_item"],
                "inst_nm_kr": row["inst_nm_kr"],
                "inst_nm_en": row["inst_nm_en"],
                "use_yn": row["use_yn"],
                "reg_dt": row["reg_dt"].isoformat() if row["reg_dt"] else None,
            }
            for row in rows
        ]
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"계측기 조회 오류: {str(e)}")


@router.get("/iace/part-categories")
def get_part_categories(db: Session = Depends(get_db)):
    """부품군별 I-ACE 카테고리 조회 - tbi_iace_partcat. 대분류 / 부품군 / 자율제어 대분류(EN·KR)."""
    try:
        rows = db.execute(text("""
            SELECT category_en, category_kr, part_group_en, part_group_kr, iace_category_en, iace_category_kr
            FROM tbi_iace_partcat
            ORDER BY category_en, part_group_en
        """)).mappings().all()
        items = [dict(row) for row in rows]
        return {"items": items, "total": len(items)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"부품군 카테고리 조회 오류: {str(e)}")
