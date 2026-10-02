# Pokemon TCG Live Lab

An open project for exploring **Pokemon TCG Live** through reproducible card
data, PostgreSQL search, deck validation, seeded simulation, and automated
decision-making.

## Project direction

The current checkpoints include:

1. A commit-pinned catalog with structured legality evidence.
2. An idempotent PostgreSQL importer and ranked fuzzy search API.
3. Deck creation, Live-list importing, exporting, and format validation.
4. Versioned machine-readable rules and parsed card-effect records.
5. A deterministic seeded simulator with three action-selection policies.
6. A deterministic starter-deck optimizer whose results can be saved and
   simulated.
7. Daily catalog synchronization, Markdown change reports, tests, and GitHub
   Actions.
8. A versioned interactive Arena for playing a saved Standard deck against a
   deterministic local opponent.

The initial automation target is the local simulator, so experiments remain
repeatable and do not depend on controlling the Pokemon TCG Live client.

Architectural decisions and open questions are maintained in
[`ARCHITECTURE.md`](ARCHITECTURE.md).
Reusable interface components and visual rules are maintained in
[`DESIGN.md`](DESIGN.md).

## Why this does not scrape pokemon.com HTML

The official card-search page is protected by a bot challenge and its HTML is
not a stable data contract. This project instead downloads a commit-pinned
snapshot from the public `PokemonTCG/pokemon-tcg-data` repository, the source
behind the Pokemon TCG API. A small, auditable policy file then applies the
official TCG Live support rules.

This distinction matters:

- **Playable in TCG Live** means implemented by the digital game.
- **Standard legal** is the rotating competitive format.
- **Expanded legal** is a separate format and is not identical to the current
  TCG Live Expanded Beta card pool.

The outputs preserve all three concepts instead of merging them.

## Current Live policy

The policy in `src/ptcgl_catalog/live_policy.json` includes released English
sets from Sun & Moon onward. It excludes `cel25c` (Celebrations: Classic
Collection), which Pokemon Support still describes as transferable but not
playable. The 2026 `me55c` Classic Collection is included because the official
TCG Live launch announcement explicitly puts it in the game.

Policy evidence:

- [Pokemon TCG Live Migration FAQ](https://support.pokemon.com/hc/en-us/articles/6489934466708-Pok%C3%A9mon-TCG-Live-Migration-FAQ-from-the-Pok%C3%A9mon-TCG-Online)
- [30th Celebration expansion release](https://community.pokemon.com/en-us/discussion/26004/pokemon-tcg-30th-celebration-expansion-release)
- [30th Celebration developer letter](https://community.pokemon.com/en-us/discussion/26006/letter-to-the-community-september-15-2026)

## Run it

Requires Python 3.11 or newer, PostgreSQL 17, and Docker for the documented
local setup.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
docker compose up -d db
ptcgl-catalog sync --as-of 2026-09-30
ptcgl-catalog import-db
```

Or without installing:

```bash
PYTHONPATH=src python -m ptcgl_catalog sync --as-of 2026-09-30
```

Generated files:

- `data/cards.jsonl` — full source records plus catalog metadata
- `data/cards.csv` — flat analysis-friendly table
- `data/cards.sqlite3` — searchable local database with the raw JSON retained
- `data/sets.json` — selected set metadata
- `data/manifest.json` — source commit, policy, counts, warnings, and SHA-256 hashes

Import the generated snapshot into PostgreSQL with an idempotent transaction:

```bash
ptcgl-catalog import-db
```

Running the import again updates the same catalog run and upserts the same card
IDs; it does not duplicate cards or catalog metadata.

## Open the card browser

The project includes a responsive local interface with card search and a Deck
Lab for importing, validating, saving, exporting, optimizing, and simulating
decks:

```bash
PYTHONPATH=src python3 -m ptcgl_catalog serve
```

Then open [http://localhost:8000](http://localhost:8000). Opening `index.html`
directly from Finder will not work because browsers prevent local HTML files
from fetching the generated catalog. The local server also powers the
**Sync cards** button and binds to `127.0.0.1` by default.

The browser queries PostgreSQL through `/api/cards`; it does not download the
complete JSONL catalog. PostgreSQL provides full-text ranking, trigram fuzzy
name matching, filters, sorting, and pagination. Standard-legal cards are the
default view.

Open [http://localhost:8000/deck.html](http://localhost:8000/deck.html) for the
Deck Lab. It starts with an empty Standard deck beside the complete searchable
Standard catalog. Card search, set and type filters, sorting, progressive
loading, copy limits, composition counts, imported lists, validation, and
saved decks all use PostgreSQL-backed data rather than a browser-side catalog.
Functionally identical reprints collapse into one library entry; opening its
print picker selects the exact artwork, set, and collector number for the deck.

Open [http://localhost:8000/arena.html](http://localhost:8000/arena.html) for
the interactive Arena. It can use any saved, valid 60-card Standard deck from
the catalog, regardless of ownership. Arena `0.1.0` enforces the documented
core flow and labels attacks whose card-specific text is only partially
supported; see the in-game Rules coverage panel before interpreting results.

Open [http://localhost:8000/review.html](http://localhost:8000/review.html) for
the password-protected card-rule review queue. Configure a local password of at
least 12 characters before starting the server:

```bash
export PTCGL_REVIEW_PASSWORD='choose-a-long-private-password'
ptcgl-catalog serve
```

The password is read only from the process environment. It is not stored in
PostgreSQL, an approval record, or Git. A successful login receives an
eight-hour, HTTP-only, same-site signed cookie. The local server remains bound
to `127.0.0.1`; a hosted version must use TLS and a real identity provider.

## Local PostgreSQL

PostgreSQL runs locally through Docker and uses the initial search-ready schema
in `db/init/001_schema.sql`:

```bash
cp .env.example .env
docker compose up -d db
docker compose ps
```

The default local connection is
`postgresql://ptcgl:ptcgl_local@localhost:5432/ptcgl`. Change the values in
`.env` when needed; `.env` is ignored by Git. Hosted environments will use the
same `DATABASE_URL` interface with credentials supplied by the host.

Example PostgreSQL query:

```bash
docker compose exec db psql -U ptcgl -d ptcgl -c \
  "SELECT name, set_name, number FROM cards WHERE standard_status = 'legal' AND name ILIKE '%Pikachu%' ORDER BY release_date DESC;"
```

## API checkpoints

- `GET /api/cards` — fuzzy/full-text search, filters, sorting, pagination
- `GET /api/card-prints?group=...` — expand one gameplay-equivalent print group
- `GET /api/sets` and `GET /api/status` — catalog metadata
- `POST /api/sync` — local guarded download and PostgreSQL import
- `POST /api/decks/validate` and `POST /api/decks` — validate and save a deck
- `GET /api/decks/{id}/export` — Pokémon TCG Live compatible text export
- `GET /api/rules` and `GET /api/cards/{id}/effects` — rule/effect models
- `POST /api/simulations` — seeded matchup simulation
- `POST /api/optimize` — deterministic heuristic deck construction
- `POST /api/arena/sessions` — start a seeded interactive Arena match
- `GET /api/arena/sessions/{id}` — read the current match state
- `POST /api/arena/sessions/{id}/actions` — apply one server-approved action
- `GET /api/rule-coverage` — AI and human-review coverage counts
- `GET /api/reviews` — authenticated human review queue
- `POST /api/reviews/{id}/decision` — authenticated approval or rejection

Run the automated checks with:

```bash
pytest -q
node --check app.js
node --check deck.js
node --check arena.js
```

## Scheduled synchronization

The `Catalog sync` GitHub Action runs daily and can also be started manually.
It downloads an immutable source snapshot, imports it into a clean PostgreSQL
service, runs tests, creates `reports/latest.md`, and opens a pull request only
when the saved catalog baseline changes. Generate the same report locally with:

```bash
ptcgl-catalog report --update-baseline
```

### AI rule pipeline

Card text moves through two independent gates:

1. A nightly Codex task works directly in this repository, creates versioned
   JSON candidates, and validates card ID, source-text hash, abilities,
   attacks, attack costs, printed damage, rule boxes, triggers, conditions,
   and supported operations.
2. A password-authenticated reviewer compares the source text with the
   executable JSON and approves or rejects it. Every decision is written to an
   append-only audit table.

An AI-passed rule is executable but remains a candidate. Only a version that
passes both automated validation and manual approval is trusted. The first 10
Standard-legal cards are installed as AI-passed and pending manual review. The
nightly task never grants human approval and never needs an OpenAI API key in
this project.

The card browser shows rule confidence without removing cards from Deck Lab or
the testing arena:

- `Not checked`: neither automated nor human validation has passed.
- `AI checked`: automated validation passed; human review is pending.
- `Human checked`: a reviewer approved it, but automated validation did not pass.
- Pokéball check: both validations passed; this is the trusted state.

Open `/review.html` to compare the printed text and executable JSON. Approval
requires `PTCGL_REVIEW_PASSWORD`; every decision remains auditable.

Validate and import candidates created by Codex with:

```bash
ptcgl-catalog import-rules
```

The Codex desktop automation handles the reasoning pass each night from the
checked-out repository. GitHub Actions remains responsible only for catalog
synchronization, PostgreSQL import verification, tests, and change reports.

## Simulator boundary

`prototype-0.1.0` is deterministic for the same decks, seed, rules version,
and policies. It currently handles setup, draws, Basic Pokémon promotion,
parsed draw effects, printed base damage, knockouts, prizes, and deck-out. It
does not yet model energy costs, evolution, Weakness, Resistance, Retreat,
Bench timing, or the long tail of card-specific effects. Every simulation
result states these limitations so prototype output is not mistaken for a
complete TCG rules judgment.

Interactive Arena `arena-0.4.0` is a separate engine checkpoint. It starts with
the official pregame sequence: coin call, the winner's first-or-second choice,
seven-card opening hands, repeated no-Basic mulligans, optional bonus draws,
player-selected Active and Bench Pokémon, and six Prize cards. It then enforces
Energy costs, evolution timing, one Energy attachment and retreat per turn,
Weakness, Resistance, Knock Outs, Prize taking, promotion choice, first-turn
restrictions, and the principal win conditions. Its opponent is
`simple-ai-0.1.0`: a deterministic setup, attach, and highest-printed-damage
policy. Each side has a separate server-authoritative 20-minute Pokémon TCG
Live match clock, and reaching zero is a game loss.

For every processed card, Arena loads the newest AI-passed program matching the
card's current source-text hash. Review status does not disable exploratory
play. Every execution records the immutable program hash, database version,
rule ID, primitive operations, review status, and result. Its stable key is
`cardId:sourceTextHash:programHash:ruleId`. The Match details
drawer lets a player flag that exact execution as incorrect; Rule Review shows
the captured trace beside the card version. Unprocessed attacks remain visibly
partial and say `base damage only`. Generated rule choices currently use a
deterministic policy when an explicit board target is not present; replacing
those policy choices with player prompts does not change the rule identity.

Universal rules constants and coverage live in
`src/ptcgl_catalog/game_rules.py`. Executable card programs remain in the
separate versioned card-rule pipeline, while the Arena event adapter executes
their effects. The browser does not duplicate game rules in JavaScript; it
renders legal actions, state, and rule traces supplied by the Python engine.

The core turn and setup behavior follows the
[official Pokémon Trading Card Game rulebook](https://tcg.pokemon.com/assets/img/global/tcg-rulebook/ME02_Web_Rulebook_en-us_HiRes.pdf).
Card-specific text, official rulings, and interaction tests remain separately
versioned so incomplete coverage is visible instead of being treated as a
complete rules judgment.

## Accuracy model

Every run resolves the source branch to an exact 40-character Git commit, then
downloads the archive at that commit. Future-dated sets are excluded by
`--as-of`. IDs are checked for uniqueness, required fields are validated, and
every generated artifact is checksummed. Source metadata/card-count mismatches
are surfaced as warnings because promo and secret-card totals are sometimes
different from the number of published rows.

The project does **not** scrape a logged-in player's collection, automate the
game client, or download card images. Image URLs remain in the catalog.
