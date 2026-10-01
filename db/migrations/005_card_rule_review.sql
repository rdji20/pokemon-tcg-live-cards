CREATE TABLE IF NOT EXISTS card_rule_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    card_id text NOT NULL REFERENCES cards(id),
    source_text_hash text NOT NULL,
    schema_version integer NOT NULL,
    ai_provider text NOT NULL,
    ai_model text NOT NULL,
    ai_response_id text,
    program jsonb NOT NULL,
    automated_checks jsonb NOT NULL,
    ai_status text NOT NULL CHECK (ai_status IN ('passed', 'failed')),
    manual_status text NOT NULL DEFAULT 'pending'
        CHECK (manual_status IN ('pending', 'approved', 'rejected')),
    reviewer text,
    review_note text,
    reviewed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (card_id, source_text_hash, schema_version, ai_provider, ai_model)
);

CREATE INDEX IF NOT EXISTS card_rule_versions_queue_idx
    ON card_rule_versions (manual_status, ai_status, created_at);
CREATE INDEX IF NOT EXISTS card_rule_versions_card_idx
    ON card_rule_versions (card_id, created_at DESC);

CREATE TABLE IF NOT EXISTS card_rule_review_audit (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_version_id uuid NOT NULL REFERENCES card_rule_versions(id) ON DELETE CASCADE,
    action text NOT NULL,
    actor text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
