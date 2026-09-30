ALTER TABLE cards ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;
ALTER TABLE cards ADD COLUMN IF NOT EXISTS catalog_run_id bigint REFERENCES catalog_runs(id);
ALTER TABLE cards ADD COLUMN IF NOT EXISTS live_status text NOT NULL DEFAULT 'unknown';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS standard_status text NOT NULL DEFAULT 'unknown';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS expanded_status text NOT NULL DEFAULT 'unknown';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS live_expanded_status text NOT NULL DEFAULT 'unknown';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS legality_evidence jsonb NOT NULL DEFAULT '{}';
ALTER TABLE cards ADD COLUMN IF NOT EXISTS verified_at timestamptz;

CREATE INDEX IF NOT EXISTS cards_active_idx ON cards (active) WHERE active;
CREATE INDEX IF NOT EXISTS cards_live_status_idx ON cards (live_status);
CREATE INDEX IF NOT EXISTS cards_standard_status_idx ON cards (standard_status);
CREATE INDEX IF NOT EXISTS cards_live_expanded_status_idx ON cards (live_expanded_status);

