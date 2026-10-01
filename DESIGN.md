# Product design system

This file is the source of truth for reusable interface components in Pokémon
TCG Live Lab. New screens should compose these components instead of creating
one-off panels, badges, or decorative cards.

Last updated: 2026-10-01

## Visual direction

- Use Roboto throughout. Do not use italics or artificial letter spacing.
- Let real Pokémon card artwork provide color and identity.
- Keep the application frame neutral: white, charcoal, soft gray, and thin
  borders. Pokémon red, blue, and yellow are functional accents, not large
  decorative fills.
- Pokéball geometry is the shared brand motif. Use it for states, actions, and
  empty states, never as background clutter.
- Standalone decorative or status dots are prohibited. Do not use a colored
  circle as a live, active, loading, or section indicator. When a compact game
  identity mark is necessary, use the complete Pokéball component with its
  dividing line and center button.
- Avoid generic dashboard cards. A bordered rectangle must represent a real
  object or workflow: a deck box, a card lineup, a review record, or a result.
- Do not use colored strips on top of containers as decoration.

## Component rules

### Global header

The global header contains the TCG Live mark, primary navigation, and one small
page-level status. Its height and navigation order remain consistent across
Cards, Deck Lab, Arena, and Rule Review.

### Deck tile

Implementation: `TcgComponents.deckTile()` in `components.js`.

The Deck Tile represents a saved physical deck, not a generic data card. It
uses three actual card images from the deck as its primary visual, followed by
the deck name, format/readiness, Pokémon-Trainer-Energy composition, and one
clear action. The 60-card count lives inside the visual deck area.

Rules:

- Never add a decorative top border or status badge.
- Prefer Pokémon cards with images, then Trainer and Energy cards.
- Show no more than three preview cards.
- The whole component may grow with its container, but its internal hierarchy
  and action placement must stay unchanged.
- At narrow widths, stack artwork above information; do not shrink card art
  until it is illegible.

### Card lineup

Implementation: `TcgComponents.cardLineup()` in `components.js`.

The Card Lineup displays up to five real cards for matchup comparison. It is
used by both sides of Test Arena. Each card shows its name and deck quantity.
Cards may lift slightly on hover, but must remain readable without interaction.

### Deck box

The Deck Box is the active 60-card workspace. It owns the format, deck name,
deck-list editor, composition counters, validation action, and save action.
Starting blank is the default. Quick Build is optional and must never silently
replace the current draft.

### Quick Build

Quick Build is a secondary blue tool beside the Deck Box. Its yellow action
and small Pokéball distinguish generation from manual construction. Generated
lists remain drafts until validation succeeds.

### Test Arena

Test Arena puts actual card lineups before configuration controls. Deck and
strategy selectors are secondary and appear below the matchup. Neither side
selects a saved deck automatically.

### Arena board

Implementations: `TcgComponents.battlePokemon()` and
`TcgComponents.handCard()` in `components.js`; composition in `arena.html`.

The Arena Board is a playable table, not a dashboard panel. The opponent field
occupies the top half, the player field and interactive hand occupy the bottom,
and the battle line separates them. It uses real card images for both Active
spots, ten Bench spots, and the player's hand. Prize, deck, discard, and hidden
opponent-hand zones use compact card geometry. The dark play surface has a
restrained red player side and blue opponent side without tinting card artwork.

Rules:

- Legal actions come from the server, but they are expressed through the game
  objects instead of a permanent action-button strip.
- Hand cards are the primary controls. Selecting or dragging a card highlights
  valid field targets; clicking or dropping onto the target completes Energy
  attachment, evolution, Benching, or a targeted Trainer effect.
- Clicking the Active Pokémon opens only its current attacks and enables valid
  retreat targets. End Turn is a single persistent HUD action.
- Hand artwork must remain fully visible at rest and while selected. Never crop
  a card to make the hand fit; use horizontal scrolling instead.
- Any partially supported attack says `base damage only` in its action label.
- Never represent an ignored card effect as executed.
- Match and opponent-policy versions remain visible before play begins.
- The game table owns its fullscreen control and must return to the exact same
  state when fullscreen closes. The `F` shortcut mirrors that control.
- Pregame uses a focused overlay only for the coin call, turn-order choice,
  and optional mulligan draw. Coin choices look and behave like physical coin
  faces, never generic form buttons. Active and Bench setup happens on the
  board by clicking highlighted cards in the real hand; one `Ready` action
  finishes placement. Setup Pokémon remain face down until both players reveal.
- Arena controls use a locked high-contrast palette. Hover, selected, disabled,
  and keyboard-focus states must never reduce label contrast.
- At narrow widths, preserve horizontally scrollable hands and Benches rather
  than shrinking the cards until their art is illegible.

### Validation result

Validation results use text plus a restrained blue success or red error state.
They must state what passed or failed; color alone never communicates status.

### Buttons and inputs

- Primary actions use charcoal.
- Generation uses yellow only inside Quick Build.
- Secondary actions use a white surface and neutral border.
- Inputs use the same radius and focus treatment across screens.
- Labels remain plain Roboto text with normal spacing.

## Reuse contract

Shared markup generators live in `components.js` and expose the frozen global
`TcgComponents`. Component-specific styles live with the consuming page until
the component is used on a second page; at that point move its styles into a
shared component stylesheet. Data preparation belongs in the page controller,
while escaping, card selection, and component markup belong in the component.

When a component changes, update this file in the same commit. If a new UI
pattern cannot be described here in one clear paragraph, it is probably not
ready to become a reusable component.
