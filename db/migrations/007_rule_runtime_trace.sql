ALTER TABLE card_rule_versions
    ADD COLUMN IF NOT EXISTS program_hash text,
    ADD COLUMN IF NOT EXISTS supersedes_id uuid REFERENCES card_rule_versions(id);

UPDATE card_rule_versions
SET program_hash = md5(program::text)
WHERE program_hash IS NULL;

ALTER TABLE card_rule_versions
    ALTER COLUMN program_hash SET NOT NULL;

ALTER TABLE card_rule_versions
    DROP CONSTRAINT IF EXISTS card_rule_versions_card_id_source_text_hash_schema_version_ai_prov_key;
ALTER TABLE card_rule_versions
    DROP CONSTRAINT IF EXISTS card_rule_versions_card_id_source_text_hash_schema_version__key;

CREATE UNIQUE INDEX IF NOT EXISTS card_rule_versions_program_idx
    ON card_rule_versions (card_id, source_text_hash, schema_version, program_hash);

CREATE TABLE IF NOT EXISTS card_rule_reports (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    rule_version_id uuid NOT NULL REFERENCES card_rule_versions(id),
    card_id text NOT NULL REFERENCES cards(id),
    rule_id text,
    arena_version text NOT NULL,
    session_id uuid,
    trace jsonb NOT NULL DEFAULT '{}',
    reason text NOT NULL,
    detail text NOT NULL DEFAULT '',
    reporter text NOT NULL DEFAULT 'arena-player',
    status text NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'resolved', 'dismissed')),
    resolution_note text,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE INDEX IF NOT EXISTS card_rule_reports_open_idx
    ON card_rule_reports (rule_version_id, status, created_at DESC);
