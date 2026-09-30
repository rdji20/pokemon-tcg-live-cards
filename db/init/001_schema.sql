CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS catalog_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_repository text NOT NULL,
    source_commit text NOT NULL,
    policy jsonb NOT NULL,
    as_of date NOT NULL,
    imported_at timestamptz NOT NULL DEFAULT now(),
    set_count integer NOT NULL CHECK (set_count >= 0),
    card_count integer NOT NULL CHECK (card_count >= 0),
    UNIQUE (source_repository, source_commit, as_of)
);

CREATE TABLE IF NOT EXISTS sets (
    id text PRIMARY KEY,
    name text NOT NULL,
    series text NOT NULL,
    release_date date NOT NULL,
    printed_total integer,
    total integer,
    raw_data jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS cards (
    id text PRIMARY KEY,
    set_id text NOT NULL REFERENCES sets(id),
    set_name text NOT NULL,
    name text NOT NULL,
    supertype text,
    subtypes text[] NOT NULL DEFAULT '{}',
    types text[] NOT NULL DEFAULT '{}',
    hp integer,
    number text,
    rarity text,
    artist text,
    regulation_mark text,
    release_date date NOT NULL,
    standard_legal boolean NOT NULL DEFAULT false,
    expanded_legal boolean NOT NULL DEFAULT false,
    image_small text,
    image_large text,
    rules_text text NOT NULL DEFAULT '',
    raw_data jsonb NOT NULL,
    search_document tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(name, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(set_name, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(rules_text, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(artist, '')), 'C') ||
        setweight(to_tsvector('simple', coalesce(id, '')), 'C')
    ) STORED
);

CREATE INDEX IF NOT EXISTS cards_search_document_idx
    ON cards USING gin (search_document);
CREATE INDEX IF NOT EXISTS cards_name_trigram_idx
    ON cards USING gin (name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS cards_set_id_idx
    ON cards (set_id);
CREATE INDEX IF NOT EXISTS cards_release_date_idx
    ON cards (release_date DESC);
CREATE INDEX IF NOT EXISTS cards_standard_legal_idx
    ON cards (standard_legal) WHERE standard_legal;
CREATE INDEX IF NOT EXISTS cards_expanded_legal_idx
    ON cards (expanded_legal) WHERE expanded_legal;

