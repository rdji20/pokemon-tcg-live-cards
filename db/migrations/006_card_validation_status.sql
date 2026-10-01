ALTER TABLE cards ADD COLUMN IF NOT EXISTS source_text_hash text;

CREATE INDEX IF NOT EXISTS cards_source_text_hash_idx
    ON cards (id, source_text_hash);

