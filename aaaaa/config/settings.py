"""
시스템 설정 관리
- API URL, 경로, 임계값 등 중앙 집중 관리
"""

import os
from dataclasses import dataclass, field
from typing import Dict, Optional
from pathlib import Path


@dataclass
class Settings:
    """시스템 설정 클래스"""
    
    # ============================================
    # API 설정
    # ============================================
    API_BASE_URL: str = "http://tapsbat.sec.samsung.net/tapsbatch/apis/chatBot"
    
    @property
    def qty_url(self) -> str:
        """수량 데이터 API URL"""
        return f"{self.API_BASE_URL}/getGmesSet"
    
    @property
    def defect_url(self) -> str:
        """불량 데이터 API URL"""
        return f"{self.API_BASE_URL}/getGmesDetl"
    
    @property
    def leave_url(self) -> str:
        """불량 이탈 데이터 API URL"""
        return f"{self.API_BASE_URL}/getProcDetectTask"
    
    # API 공통 파라미터
    API_USER_ID: str = "sunbae.lee"
    API_SYSTEM_GBN_GMQIS: str = "GMQIS_GMES"
    API_SYSTEM_GBN_PROC: str = "GMQIS_PROC_TASK"
    API_PRODUCT: str = "TELE"
    
    # ============================================
    # VLM LLM 설정 (Qwen 모델)
    # ============================================
    VLM_BASE_URL: str = os.getenv("VLM_BASE_URL", "")
    VLM_API: str = os.getenv("VLM_API", "")
    
    # ============================================
    # 경로 설정
    # ============================================
    BASE_DIR: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2])
    VEND_DATA_DIR: Path = field(default_factory=lambda: Path(__file__).resolve().parents[2] / "data")
    # IQC_DATA_PATH: Path = Path(r"C:\Users\sunbae.lee\Downloads\0_IQCDATA.xlsx")
    OUTPUT_DIR: Optional[Path] = field(default_factory=lambda: Path(__file__).resolve().parents[2] / "result")

    
    def __post_init__(self):
        """초기화 후 경로 설정"""
        if self.OUTPUT_DIR is None:
            self.OUTPUT_DIR = self.BASE_DIR / "output"
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    @property
    def vendor_code_path(self) -> Path:
        """협력사 코드 매핑 파일 경로"""
        return self.VEND_DATA_DIR / "vendor.parquet"
    
    @property
    def maker_abbr_path(self) -> Path:
        """제조사 약어 매핑 파일 경로"""
        return self.VEND_DATA_DIR / "MAKER_ABBR.parquet"
    
    # ============================================
    # 품질등급 임계값 설정
    # ============================================
    # 카테고리별 PPM 경고 기준
    CATEGORY_PPM_THRESHOLD: Dict[str, int] = field(default_factory=lambda: {
        'GLASS': 7000,
        '기구': 7000,
        'APS': 7000
    })
    DEFAULT_PPM_THRESHOLD: int = 1000
    
    # PPM 트렌드 임계값
    PPM_TREND_HIGH: float = 3.0  # 긴급 상황 임계값
    PPM_TREND_WARNING: float = 1.5  # 주의 상황 임계값
    
    # 불량 건수 임계값
    DEFECT_QTY_EMERGENCY: int = 5
    DEFECT_QTY_WARNING: int = 3
    INSP_DEF_QTY_EMERGENCY: int = 10
    
    # 개선 판정 최소 검사수
    MIN_RECOVERY_INSP_QTY: int = 100
    # 신규 1등급: 월간+주간 검사수, 두 기간 무불량 및 AB 0건 필요
    GRADE_ONE_MIN_INSP_QTY: int = 100

    # ============================================
    # PPM 판정 최소 표본수 가드
    # - 소표본에서 불량 1건으로 PPM이 폭등해 등급이 요동치는 문제 방지
    # - 긴급 조건(불량 10건 이상)과 동일한 '건수 병행' 방식
    # ============================================
    # PPM 경고 판정(기초레벨 4~5등급, 주의 페널티)에 필요한 최소 불량 건수
    MIN_DEF_QTY_FOR_PPM_ALERT: int = 2
    # 검사이력으로 인정하는 최소 검사수 (미만은 이력 없음 취급)
    MIN_INSP_QTY_FOR_LEVEL: int = 5

    # ============================================
    # AB 이상감지 집계 설정 (tbi_stat_vendor 기반, ACE 크롤링 대체)
    # ============================================
    # AB_CNT 집계 롤링 윈도우 (일)
    # 윈도우 내 제출 이력이 없으면 결측(NaN) 처리
    # → 최근 데이터 입수이력 없는 부품은 1등급 불가 + 무검사유지 혜택 불가
    AB_CNT_WINDOW_DAYS: int = 14
    # 조기 해제: 마지막 이상감지 이후 연속 정상 LOT 수 (감지 3LOT 연속과 대칭)
    AB_CLEAR_NORMAL_LOTS: int = 3

    # ============================================
    # 등급 래칫(단계 하향) 설정
    # - 악화는 즉시 반영, 개선은 연속 검증 후 1단계씩만 하향
    # ============================================
    GRADE_RATCHET_ENABLED: bool = True
    # 1회성 래칫 우회 만료일 (YYYY-MM-DD, .env). 실행일이 이 날짜 이하이면 래칫을 건너뛰고
    # 하이브리드 등급을 그대로 반영한다 (집계 기준 변경 반영용). 비어 있으면 미적용, 날짜가 지나면 자동 무효.
    GRADE_RATCHET_BYPASS_UNTIL: str = field(default_factory=lambda: os.getenv('GRADE_RATCHET_BYPASS_UNTIL', '').strip())
    # 하향에 필요한 연속 개선신호 횟수 (당일 포함)
    # [수정] 4 → 2: 개선 판정 완화 (더 빠른 개선 반영)
    GRADE_RECOVERY_CONSECUTIVE: int = 2
    # 하향 시 요구되는 최소 검사수 (MONTH+WEEK 합계)
    # 회복에는 실제 검사수 충족이 필요하며, 대체 신호로 검사수를 갈음하지 않음
    GRADE_RECOVERY_MIN_INSP_QTY: int = 50
    # 회복유지 한도: 개선신호가 이 횟수 이상 연속되고 MONTH·WEEK 입고가 모두 없으면
    # 검사수 조건 없이 하이브리드 등급을 그대로 반영 (거래 중단 부품의 등급 고착 방지)
    GRADE_RECOVERY_MAX_HOLD: int = 15
    # 이력 조회 시 최근 업로드 수 (연속 횟수보다 크게)
    GRADE_HISTORY_LOOKBACK_UPLOADS: int = 10

    # ============================================
    # 무검사(검사감소) 등급유지 설정
    # - 자율제어로 검사가 감소한 우수 부품이 '검사이력 없음'만으로
    #   3등급 복귀하는 진동 루프 방지
    # - 무검사 기간에는 대체 신호(AB_CNT, LEAVE_CNT, 공정불량)로 감시 대체
    # ============================================
    NO_INSP_HOLD_ENABLED: bool = True
    # 유지 혜택 대상: 이전 최종등급이 이 값 이하인 부품만
    NO_INSP_HOLD_MAX_GRADE: int = 2
    # 연속 무검사유지 한도 (실행 횟수, 일 1회 실행 기준)
    # [수정] 70회(약 10주) → 52회(약 1년): 품질 모니터링 강화
    # 초과 시 확인검사 요구 → 검사 실적 없으면 상향 반영
    NO_INSP_HOLD_MAX_CONSECUTIVE: int = 52

    # ============================================
    # ML 모델 설정
    # ============================================
    ML_RISK_THRESHOLD: float = 0.9  # ML High Risk 판정 임계값
    ML_PRECISION_THRESHOLD: float = 0.3  # 모델 선택 최소 정밀도
    
    # ML Feature 설정 (카테고리별)
    ML_FEATURES: Dict[str, list] = field(default_factory=lambda: {
        'CATEGORY_1': ['TOTAL_INSP_PPM', 'MONTH_INSP_PPM', 'WEEK_INSP_PPM', 'TOTAL_FAIL_RATE', 'VEND_RISK_SCORE'],
        'CATEGORY_2': ['TOTAL_INSP_PPM', 'MONTH_INSP_PPM', 'WEEK_INSP_PPM', 'TOTAL_FAIL_RATE', 'VEND_RISK_SCORE'],
        'CATEGORY_3': ['TOTAL_INSP_PPM', 'MONTH_INSP_PPM', 'WEEK_INSP_PPM', 'TOTAL_FAIL_RATE', 'PRODC_PPM_WEEK', 'PRODC_PPM_MONTH', 'VEND_RISK_SCORE'],
        'CATEGORY_5': ['TOTAL_INSP_PPM', 'MONTH_INSP_PPM', 'WEEK_INSP_PPM', 'TOTAL_FAIL_RATE', 'PRODC_PPM_WEEK', 'PRODC_PPM_MONTH', 'VEND_RISK_SCORE']
    })
    
    # ML 카테고리 매핑
    ML_CATEGORY_MAPPING: Dict[str, tuple] = field(default_factory=lambda: {
        'CATEGORY_1': ('기구', 'GLASS'),
        'CATEGORY_2': ('APS', 'Etc', 'TOTAL SOLUTION', '응용'),
        'CATEGORY_3': ('Note PC', '디스플레이'),
        'CATEGORY_5': ('전기',)
    })
    
    # ============================================
    # AI 분석 설정
    # ============================================
    AI_CONFIDENCE_HIGH: float = 0.8  # AI 가중치 100%
    AI_CONFIDENCE_MEDIUM: float = 0.5  # AI 가중치 70%
    
    AI_WEIGHT_HIGH: float = 1.0  # 신뢰도 높을 때 AI 가중치
    AI_WEIGHT_MEDIUM: float = 0.7  # 신뢰도 중간일 때 AI 가중치
    
    AI_MAX_WORKERS: int = 15  # 병렬 처리 스레드 수
    AI_REQUEST_DELAY: float = 0.1  # API 요청 간 지연 시간
    AI_SAVE_INTERVAL: int = 500  # 중간 저장 간격
    
    # ============================================
    # 데이터 수집 설정
    # ============================================
    RECENT_DAYS: int = 14  # 최근 데이터 기간 (일)
    PAST_DAYS_START: int = 44  # 과거 데이터 시작일
    PAST_DAYS_END: int = 15  # 과거 데이터 종료일
    
    # ============================================
    # 환경 설정
    # ============================================
    def setup_environment(self):
        """환경 변수 설정"""
        os.environ['no_proxy'] = '.sec.samsung.net'
        os.environ['REQUESTS_CA_BUNDLE'] = ''


# 싱글톤 인스턴스
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """설정 인스턴스 반환 (싱글톤)"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance