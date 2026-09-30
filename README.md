# Pokemon TCG Live Lab

An open project for exploring **Pokemon TCG Live** through reproducible card
data, deck simulation, and automated decision-making. The current first stage
builds an accurate English-language card catalog from an immutable source
snapshot and exports it as JSONL, CSV, and SQLite.

## Project direction

The card catalog is the data foundation. Planned work includes:

1. A machine-readable rules and card-effects model.
2. Deck representation, validation, importing, and exporting.
3. A deterministic game-state and turn simulator.
4. Automated deck construction and matchup evaluation.
5. Decision policies for choosing actions in simulated games.
6. Reproducible experiments comparing decks, strategies, and policy versions.

The initial automation target is the local simulator, so experiments remain
repeatable and do not depend on controlling the Pokemon TCG Live client.

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

Requires Python 3.11 or newer and no runtime dependencies.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
ptcgl-catalog sync --as-of 2026-09-30
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

## Open the card browser

The project includes a responsive local web interface with search, filters,
pagination, card images, and detailed card views:

```bash
python3 -m http.server 8000
```

Then open [http://localhost:8000](http://localhost:8000). Opening `index.html`
directly from Finder will not work because browsers prevent local HTML files
from fetching the generated catalog.

Example query:

```bash
sqlite3 data/cards.sqlite3 \
  "SELECT name, set_name, number FROM cards WHERE standard_legal = 1 AND name LIKE '%Pikachu%' ORDER BY release_date DESC;"
```

## Accuracy model

Every run resolves the source branch to an exact 40-character Git commit, then
downloads the archive at that commit. Future-dated sets are excluded by
`--as-of`. IDs are checked for uniqueness, required fields are validated, and
every generated artifact is checksummed. Source metadata/card-count mismatches
are surfaced as warnings because promo and secret-card totals are sometimes
different from the number of published rows.

The project does **not** scrape a logged-in player's collection, automate the
game client, or download card images. Image URLs remain in the catalog.
