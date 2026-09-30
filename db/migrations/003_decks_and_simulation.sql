CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS rulesets (
    id text PRIMARY KEY,
    version text NOT NULL,
    name text NOT NULL,
    rules jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS card_effects (
    card_id text PRIMARY KEY REFERENCES cards(id),
    parser_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('parsed', 'partial', 'unparsed')),
    effects jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS decks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL,
    format text NOT NULL CHECK (format IN ('standard', 'live-expanded', 'unlimited')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS deck_cards (
    deck_id uuid NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    card_id text NOT NULL REFERENCES cards(id),
    quantity integer NOT NULL CHECK (quantity > 0 AND quantity <= 60),
    PRIMARY KEY (deck_id, card_id)
);

CREATE INDEX IF NOT EXISTS deck_cards_card_idx ON deck_cards (card_id);

CREATE TABLE IF NOT EXISTS simulation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    deck_a_id uuid REFERENCES decks(id),
    deck_b_id uuid REFERENCES decks(id),
    seed bigint NOT NULL,
    games integer NOT NULL CHECK (games > 0),
    model_version text NOT NULL,
    policy_a text NOT NULL,
    policy_b text NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS optimization_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    format text NOT NULL,
    seed bigint NOT NULL,
    algorithm_version text NOT NULL,
    parameters jsonb NOT NULL,
    result jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

