ALTER TABLE simulation_runs
    ADD COLUMN IF NOT EXISTS catalog_run_id bigint REFERENCES catalog_runs(id),
    ADD COLUMN IF NOT EXISTS ruleset_id text REFERENCES rulesets(id),
    ADD COLUMN IF NOT EXISTS ruleset_version text;

ALTER TABLE optimization_runs
    ADD COLUMN IF NOT EXISTS catalog_run_id bigint REFERENCES catalog_runs(id);
