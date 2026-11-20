"""
데이터베이스 모델 정의
- QualityGrade: 품질 등급 결과 테이블
"""

from datetime import datetime
from sqlalchemy import Column, Integer, BigInteger, String, Float, DateTime, Text, Index
from sqlalchemy import func

from database.db import Base


class QualityGrade(Base):
    """
    품질 등급 결과 테이블
    - 분석 결과 저장
    - 중복 데이터 방지 (data_hash 사용)
    """
    __tablename__ = "tbi_quality_grade"
    
    # 기본 키 (DB는 bigint)
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    
    # 업로드 정보 (DB는 text)
    upload = Column(Text, nullable=True)
    data_hash = Column(Text, nullable=True)
    
    # 자재 정보 (DB는 모두 text)
    part_cd = Column(Text, nullable=True)
    part_nm = Column(Text, nullable=True)
    maker_cd = Column(Text, nullable=True)
    vend_nm = Column(Text, nullable=True)
    vend_l_cd = Column(Text, nullable=True)
    category_l = Column(Text, nullable=True)
    category_s = Column(Text, nullable=True)
    test_level_code = Column(Text, nullable=True)
    ten_week_insp_yn = Column(Text, nullable=True)
    
    # 등급 정보 (DB는 bigint)
    rule_grade = Column(BigInteger, nullable=True)
    final_grade = Column(BigInteger, nullable=True)
    # 래칫 적용 전 하이브리드(Rule+AI) 등급 - 개선신호 연속성 판단에 사용
    hybrid_grade = Column(BigInteger, nullable=True)
    ai_suggested_grade = Column(BigInteger, nullable=True)
    ai_confidence = Column(Float, nullable=True)
    
    # 판정 사유 (DB는 text)
    level_reason = Column(Text, nullable=True)
    ai_reasoning = Column(Text, nullable=True)
    final_reason = Column(Text, nullable=True)
    
    # 품질 지표 - 입고검사 (DB는 double precision)
    total_insp_ppm = Column(Float, nullable=True)
    total_fail_rate = Column(Float, nullable=True)
    month_insp_ppm = Column(Float, nullable=True)
    month_fail_rate = Column(Float, nullable=True)
    week_insp_ppm = Column(Float, nullable=True)
    week_fail_rate = Column(Float, nullable=True)
    
    # 품질 지표 - 공정 (DB는 double precision)
    prodc_ppm_week = Column(Float, nullable=True)
    prodc_ppm_month = Column(Float, nullable=True)
    ppm_trend_ratio = Column(Float, nullable=True)
    
    # 이상 지표 (DB는 double precision)
    ab_cnt = Column(Float, nullable=True)
    leave_cnt = Column(Float, nullable=True)
    
    # 검사 수량 정보 (DB는 bigint 또는 double precision)
    total_lot_cnt = Column(BigInteger, nullable=True)
    total_gr_qty = Column(BigInteger, nullable=True)
    total_insp_qty = Column(BigInteger, nullable=True)
    total_insp_def_qty = Column(BigInteger, nullable=True)
    month_lot_cnt = Column(Float, nullable=True)
    month_gr_qty = Column(Float, nullable=True)
    month_insp_qty = Column(Float, nullable=True)
    month_insp_def_qty = Column(Float, nullable=True)
    week_lot_cnt = Column(Float, nullable=True)
    week_gr_qty = Column(Float, nullable=True)
    week_insp_qty = Column(Float, nullable=True)
    week_insp_def_qty = Column(Float, nullable=True)
    
    # 공정 데이터 (DB는 double precision 또는 text)
    defect_qty_month = Column(Float, nullable=True)
    acrs_qty_month = Column(Float, nullable=True)
    defect_qty_week = Column(Float, nullable=True)
    acrs_qty_week = Column(Float, nullable=True)
    leave_proc = Column(Text, nullable=True)
    leave_fail_nm = Column(Text, nullable=True)
    
    # 추가 정보 (DB는 text 또는 double precision)
    if_cd = Column(Text, nullable=True)
    cpk_ab = Column(Float, nullable=True)
    edge_ab = Column(Float, nullable=True)
    
    # ML 관련 (DB는 bigint)
    is_ml_high_risk = Column(BigInteger, nullable=True)
    
    # 테이블 설정 (기존 테이블 사용)
    __table_args__ = (
        Index('ix_tbi_quality_grade_data_hash', 'data_hash'),
        Index('ix_tbi_quality_grade_part_vendor_upload', part_cd, vend_l_cd, upload.desc()),
        Index('ix_tbi_quality_grade_upload', upload.desc()),
        {'extend_existing': True, 'implicit_returning': False},
    )
    
    def __repr__(self):
        return f"<QualityGrade(id={self.id}, part_cd='{self.part_cd}', grade={self.final_grade}, upload={self.upload})>"