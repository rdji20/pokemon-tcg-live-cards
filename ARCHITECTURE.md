# Architecture

This document records architectural decisions for Pokemon TCG Live Lab. It is
updated when a decision is accepted, replaced, or rejected so that the reason
for a change remains visible.

Last updated: 2026-09-30

## Goals

- Maintain an accurate, reproducible catalog of cards playable in Pokemon TCG
  Live.
- Provide fast structured and full-text card search.
- Support deck validation, game simulation, deck optimization, and automated
  decision policies.
- Run the complete development stack locally.
- Move to an online host without changing application code or data models.

## Current system

```text
PokemonTCG data snapshot
          |
          v
Python catalog builder -----> JSONL / CSV / SQLite / manifest
          |
          v
PostgreSQL importer --------> PostgreSQL search database
                                      |
                                      v
                                Local search/API service
                                      |
                          +-----------+-----------+
                          |                       |
                    Browser + Deck Lab      Rules / simulation /
                                             optimization
```

The source snapshot and generated manifest remain the reproducibility layer.
PostgreSQL is the application database, not the only copy of the source data.

## Components

### Catalog builder

`src/ptcgl_catalog` resolves the upstream branch to an exact Git commit,
downloads that immutable snapshot, applies the TCG Live policy, validates card
IDs, and writes local artifacts with checksums.

### PostgreSQL

PostgreSQL stores normalized sets and cards while retaining each full source
record in `jsonb`. It provides:

- weighted full-text search through `tsvector` and GIN;
- fuzzy card-name matching through `pg_trgm`;
- indexed filters for set, release date, and format legality;
- normalized decks, simulations, and optimization experiment results.

The initial schema is in `db/init/001_schema.sql`, with incremental migrations
in `db/migrations`. Docker Compose runs the same PostgreSQL major version
locally that we can deploy to a managed service later.

### Search/API service

The browser calls an HTTP API instead of downloading the entire card catalog.
The API owns query parsing, weighted full-text and fuzzy ranking, filtering,
sorting, and pagination. The current service uses Python's standard HTTP server
for the local application boundary; a hosted framework remains an open choice.

### Deck, rules, and simulation engine

Decks are normalized in PostgreSQL and enter the system through card IDs or a
Pokémon TCG Live text list. Validation checks deck size, four-copy rules,
Basic Pokémon, Live availability, and selected-format legality.

The simulation engine is a separate domain module and does not depend on the
browser. `prototype-0.1.0` is deterministic for the same decks, seed, rules
version, and decision policies. It intentionally exposes its incomplete rules
coverage in every result. Card effects are stored as versioned JSON generated
by an incremental parser, retaining the raw source text for future parsing.

AI-generated programs use a stricter, separately versioned rule schema. A
source-text SHA-256 binds every program to the exact abilities, attacks, and
rule-box text it was generated from. Static validation is followed by a human
approval gate; only approved versions are considered trusted rules.

## Local and hosted environments

Local development uses `compose.yaml` and a named Docker volume. Applications
connect through `DATABASE_URL`.

An online deployment will replace only the value of `DATABASE_URL` with a
managed PostgreSQL connection. Schema migrations, queries, and application code
remain the same. Passwords and hosted connection strings belong in environment
variables and are never committed.

## Data ownership and reproducibility

- Raw upstream data is identified by repository and 40-character commit.
- Every import creates a `catalog_runs` record with its policy and effective
  date.
- The complete source card remains in `cards.raw_data`.
- Derived search text may be rebuilt from `raw_data`.
- Simulation results must record the catalog run, rules version, random seed,
  deck versions, and decision-policy versions used.

## Decision log

### ADR-001: Use PostgreSQL as the application database

- Status: Accepted
- Date: 2026-09-30
- Decision: Use PostgreSQL locally and in hosted environments. Configure every
  application through `DATABASE_URL`.
- Reason: The project needs relational integrity, JSON support, full-text and
  fuzzy search, filtering, and durable experiment data. PostgreSQL provides all
  of these without introducing a separate search service yet.
- Consequence: Local development requires Docker or a native PostgreSQL
  installation. We must add migrations before the schema begins changing.

### ADR-002: Keep immutable source artifacts outside PostgreSQL

- Status: Accepted
- Date: 2026-09-30
- Decision: Preserve the commit-pinned catalog artifacts and manifest even
  after PostgreSQL becomes the main application database.
- Reason: Database contents can be migrated or modified. Immutable artifacts
  make every import auditable and reproducible.
- Consequence: PostgreSQL can always be rebuilt from a known catalog snapshot.

### ADR-003: Use PostgreSQL search before a dedicated search service

- Status: Accepted
- Date: 2026-09-30
- Decision: Begin with weighted PostgreSQL full-text search plus trigram fuzzy
  matching. Do not add Elasticsearch, OpenSearch, or a vector database yet.
- Reason: The current catalog is small, PostgreSQL can rank and filter it well,
  and one database is simpler to operate locally and online.
- Consequence: Semantic search remains a later addition. It can be introduced
  as hybrid ranking without replacing structured PostgreSQL filters.

### ADR-004: Keep simulation logic independent from the game client

- Status: Accepted
- Date: 2026-09-30
- Decision: Build decision automation against a local deterministic simulator,
  not by coupling the core engine to the Pokemon TCG Live client.
- Reason: This makes experiments reproducible, testable, and independent of
  client updates.
- Consequence: Rules and card effects must be modeled explicitly.

### ADR-005: Expose catalog refresh only through the local application server

- Status: Accepted
- Date: 2026-09-30
- Decision: The browser's manual sync button calls a same-origin endpoint on a
  server bound to `127.0.0.1`. The endpoint requires a custom request header and
  permits only one sync at a time.
- Reason: Static HTTP servers cannot start the Python catalog builder. A small
  local endpoint provides the requested UI action without exposing a refresh
  operation to the network.
- Consequence: A hosted deployment must move refreshes to an authenticated
  administrative job or scheduled worker; it must not publish this endpoint
  without access control.

### ADR-006: Store legality as sourced, independent dimensions

- Status: Accepted
- Date: 2026-09-30
- Decision: Store `live_status`, `standard_status`, `expanded_status`, and
  `live_expanded_status` separately. Every imported card also records evidence,
  the source commit, the policy date, and its verification time.
- Reason: Presence in TCG Live, Standard legality, physical Expanded legality,
  and TCG Live Expanded implementation are different facts that can change on
  different schedules.
- Consequence: Unknown or unsupported states are explicit rather than inferred
  as legal. Simulation formats can select the exact status they require.

### ADR-007: Use versioned declarative effects with raw-text fallback

- Status: Accepted
- Date: 2026-09-30
- Decision: Store parsed card operations as versioned JSON in `card_effects`.
  Mark each card `parsed`, `partial`, or `unparsed` and retain its raw text.
- Reason: Thousands of unique card wordings cannot safely be converted into
  executable behavior in one step. Explicit coverage lets the parser improve
  without silently inventing rules.
- Consequence: Simulations must report unsupported mechanics and must never
  treat a partial parse as authoritative complete behavior.

### ADR-008: Version deterministic simulation and decision policies

- Status: Accepted
- Date: 2026-09-30
- Decision: Every simulation records its seed, model version, deck IDs, game
  count, and named action policies. Initial policies are greedy damage,
  durability, and seeded random selection.
- Reason: Matchup results are useful only when they can be reproduced and
  compared across engine changes.
- Consequence: Rule improvements require a new model version rather than an
  invisible behavior change.

### ADR-009: Persist deck and experiment artifacts in PostgreSQL

- Status: Accepted
- Date: 2026-09-30
- Decision: Store normalized decks, simulation runs, and optimization runs in
  PostgreSQL while exposing import/export through the API and Deck Lab.
- Reason: Experiments need stable inputs and durable outputs that can be
  queried later.
- Consequence: Hosted deployments use the same schema; authentication and deck
  ownership must be added before multi-user hosting.

### ADR-010: Scheduled sync produces reviewed changes

- Status: Accepted
- Date: 2026-09-30
- Decision: Run a daily GitHub Action that syncs, imports, tests, and produces
  a change report. Open a pull request when the tracked baseline changes.
- Reason: Automatic discovery is useful, but legality/catalog changes should be
  visible and reviewable before becoming the repository baseline.
- Consequence: GitHub Actions needs pull-request write permission. Local manual
  sync remains available for immediate testing.

### ADR-011: Require AI and human gates for executable card rules

- Status: Accepted
- Date: 2026-09-30
- Decision: Run a scheduled Codex task directly against the repository to
  generate candidate programs in the versioned JSON schema, validate them
  against the source card and supported operation vocabulary, then require
  password-authenticated human approval. Record every approval or rejection in
  an append-only audit table. Do not call a model API from application code or
  GitHub Actions.
- Reason: Free-form card text contains timing, replacement effects, choices,
  and unusual interactions. Schema-valid AI output is useful for scale but is
  not sufficient evidence of rules accuracy.
- Consequence: AI-passed programs are executable candidates, while only human-
  approved versions are trusted. Changed source text creates a new version and
  requires another review. The local password never enters the database or
  repository; hosted review must replace it with proper user authentication.

### ADR-012: Keep rule confidence separate from testing eligibility

- Status: Accepted
- Date: 2026-09-30
- Decision: Expose four source-versioned card states: not validated, AI
  validated, human validated, and fully validated. Show a compact Pokéball
  check only for the fully validated state. Permit every catalog card in Deck
  Lab and the experimental arena regardless of its rule-validation state.
- Reason: Partial coverage must not prevent exploratory deck work, but test
  results must make the confidence of executable card behavior visible.
- Consequence: Validation badges are warnings, not deck-legality gates. Trusted
  rules require both checks. A card-text change produces a new source hash and
  clears the visible status until that exact text version is reviewed again.

## Open decisions

- Python HTTP framework and API contract.
- Migration tool and release process.
- Complete card-effect grammar and official ruling overrides.
- Full simulation state/action protocol beyond the prototype.
- Deck-search evaluation metrics and tournament matchup datasets.
- Whether semantic embeddings provide enough value after structured search.

When one of these is decided, add a new ADR. If a decision changes, add a new
ADR that supersedes the old one instead of deleting the previous reasoning.
