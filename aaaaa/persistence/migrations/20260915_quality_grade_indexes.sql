-- I_ACE quality grade storage indexes (PostgreSQL; run with autocommit).
-- Preserves rows, ID sequence, grade policy and existing hash deduplication semantics.
SET lock_timeout = '5s';
SET statement_timeout = '5min';

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS tbi_quality_grade_pkey
    ON public.tbi_quality_grade (id);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.tbi_quality_grade'::regclass AND contype = 'p'
    ) THEN
        ALTER TABLE public.tbi_quality_grade
            ADD CONSTRAINT tbi_quality_grade_pkey
            PRIMARY KEY USING INDEX tbi_quality_grade_pkey;
    END IF;
END $$;

CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tbi_quality_grade_data_hash
    ON public.tbi_quality_grade (data_hash);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tbi_quality_grade_part_vendor_upload
    ON public.tbi_quality_grade (part_cd, vend_l_cd, upload DESC);
CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_tbi_quality_grade_upload
    ON public.tbi_quality_grade (upload DESC);

ANALYZE public.tbi_quality_grade;