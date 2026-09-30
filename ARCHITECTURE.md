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
                    Browser interface       Simulation engine
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
- a future home for decks, simulations, game states, and experiment results.

The initial schema is in `db/init/001_schema.sql`, with incremental migrations
in `db/migrations`. Docker Compose runs the same PostgreSQL major version
locally that we can deploy to a managed service later.

### Search/API service

The browser calls an HTTP API instead of downloading the entire card catalog.
The API owns query parsing, weighted full-text and fuzzy ranking, filtering,
sorting, and pagination. The current service uses Python's standard HTTP server
for the local application boundary; a hosted framework remains an open choice.

### Simulation engine

The simulation engine will be a separate domain module. It may read card and
deck data through repository interfaces, but it must not depend directly on
HTTP or browser code. Simulations should be deterministic when given the same
deck lists, random seed, rules version, and decision-policy version.

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

## Open decisions

- Python HTTP framework and API contract.
- Migration tool and release process.
- Card-effect representation: typed code, declarative rules, or a hybrid.
- Simulation state model and action protocol.
- Deck-search algorithms and evaluation metrics.
- Whether semantic embeddings provide enough value after structured search.

When one of these is decided, add a new ADR. If a decision changes, add a new
ADR that supersedes the old one instead of deleting the previous reasoning.
