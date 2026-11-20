"""
데이터베이스 연결 설정
- PostgreSQL 연결 (리눅스 서버)
- 비밀번호 특수문자 처리
- SQLAlchemy + psycopg2 사용
"""

import os
from dotenv import load_dotenv
from pathlib import Path
from sqlalchemy import create_engine, text, URL
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# .env 로드
load_dotenv(Path(__file__).parent.parent / ".env")

# Stable names identify the databases; APP_ENV selects this server's reads.
APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
if APP_ENV not in {"development", "production"}:
    raise ValueError("APP_ENV must be development or production")
READ_DB_TARGET = "SERVER_DB" if APP_ENV == "production" else "IQC_DB"


def _make_engine(prefix):
    required = ("HOST", "PORT", "USER", "PASSWORD", "NAME")
    values = {key: os.getenv(f"{prefix}_{key}") for key in required}
    missing = [f"{prefix}_{key}" for key, value in values.items() if not value]
    if missing:
        raise RuntimeError("Missing DB settings: " + ", ".join(missing))
    url = URL.create("postgresql+psycopg2", username=values["USER"],
                     password=values["PASSWORD"], host=values["HOST"],
                     port=int(values["PORT"]), database=values["NAME"])
    return create_engine(url, pool_size=5, max_overflow=10, pool_pre_ping=True,
                         pool_recycle=3600, echo=False, connect_args={"connect_timeout": 10})


engine = _make_engine(READ_DB_TARGET)
_engines = {READ_DB_TARGET: engine}
DATABASE_URL = engine.url
ACTIVE_DB_HOST = engine.url.host
ACTIVE_DB_NAME = engine.url.database
# Compatibility names describe the connection actually used by existing UI diagnostics.
IQC_DB_HOST = ACTIVE_DB_HOST
IQC_DB_NAME = ACTIVE_DB_NAME
IQC_DB_PORT = engine.url.port


def get_write_engines():
    for prefix in ("IQC_DB", "SERVER_DB"):
        if prefix not in _engines:
            _engines[prefix] = _make_engine(prefix)
    identities = [(e.url.host, e.url.port, e.url.database) for e in _engines.values()]
    if len(set(identities)) != 2:
        raise RuntimeError("Development and production DB destinations must differ")
    return {name: _engines[name] for name in ("IQC_DB", "SERVER_DB")}


def scheduler_enabled():
    value = os.getenv("BATCH_SCHEDULER_ENABLED", "true" if APP_ENV == "production" else "false")
    return value.strip().lower() in {"true", "1", "yes"}


# 세션 팩토리
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base 클래스
Base = declarative_base()


def _ensure_quality_grade_columns():
    """기존 tbi_quality_grade 테이블에 신규 컬럼을 추가한다."""
    columns = {
        "test_level_code": "TEXT",
        "ten_week_insp_yn": "TEXT",
        "hybrid_grade": "BIGINT",
        "lot_recovery_state": "TEXT",
    }

    with engine.begin() as conn:
        for column_name, column_type in columns.items():
            conn.execute(text(
                f"ALTER TABLE tbi_quality_grade "
                f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
            ))


def get_db():
    """
    DB 세션 의존성
    FastAPI에서 사용: `db: Session = Depends(get_db)`
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """
    DB 초기화 (테이블 생성)
    """
    from .models import QualityGrade  # 순환 import 방지
    Base.metadata.create_all(bind=engine)
    _ensure_quality_grade_columns()
    print(f"✅ DB 초기화 완료: PostgreSQL ({IQC_DB_HOST}:{IQC_DB_PORT}/{IQC_DB_NAME})")


def test_connection():
    """
    DB 연결 테스트
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            result.fetchone()
        print(f"✅ PostgreSQL 연결 성공: {IQC_DB_HOST}:{IQC_DB_PORT}/{IQC_DB_NAME}")
        return True
    except Exception as e:
        print(f"❌ PostgreSQL 연결 실패: {e}")
        return False


def get_engine():
    """
    엔진 반환 (pandas to_sql 등에서 사용)
    """
    return engine

def ensure_lot_recovery_write_schema():
    """Add the durable state column on active batch destinations before writing."""
    from database.batch_writer import active_targets
    for target in active_targets():
        if target not in _engines:
            _engines[target] = _make_engine(target)
        with _engines[target].begin() as connection:
            connection.execute(text(
                'ALTER TABLE tbi_quality_grade '
                'ADD COLUMN IF NOT EXISTS lot_recovery_state TEXT'
            ))
