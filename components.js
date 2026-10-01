(() => {
  function escapeHtml(value = '') {
    return String(value).replace(/[&<>'"]/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[character]);
  }

  function featuredCards(cards = [], limit = 5) {
    const priority = { 'Pokémon': 0, Trainer: 1, Energy: 2 };
    return [...cards]
      .filter(card => card.image_small || card.image_large)
      .sort((left, right) => {
        const typeDifference = (priority[left.supertype] ?? 3) - (priority[right.supertype] ?? 3);
        return typeDifference || right.quantity - left.quantity || left.name.localeCompare(right.name);
      })
      .slice(0, limit);
  }

  function composition(cards = []) {
    return cards.reduce((counts, card) => {
      const key = card.supertype === 'Pokémon' ? 'pokemon' : String(card.supertype || '').toLowerCase();
      if (key in counts) counts[key] += Number(card.quantity || 0);
      return counts;
    }, { pokemon: 0, trainer: 0, energy: 0 });
  }

  function cardFigure(card, index) {
    return `
      <figure class="arena-card" style="--card-index: ${index}">
        <img src="${escapeHtml(card.image_small || card.image_large)}" alt="${escapeHtml(card.name)} card">
        <figcaption><strong>${escapeHtml(card.name)}</strong><span>×${Number(card.quantity || 0)}</span></figcaption>
      </figure>
    `;
  }

  function cardLineup(cards = [], limit = 5) {
    const featured = featuredCards(cards, limit);
    return featured.length
      ? featured.map(cardFigure).join('')
      : '<p>No card images are available for this deck.</p>';
  }

  function deckTile(summary, deck) {
    const cards = featuredCards(deck?.cards, 3);
    const counts = composition(deck?.cards);
    const format = String(summary.format || 'standard').replace('-', ' ');
    const ready = Number(summary.card_count) === 60;
    const art = cards.length ? cards.map((card, index) => `
      <img src="${escapeHtml(card.image_small || card.image_large)}" alt="" style="--preview-index: ${index}; --preview-offset: ${(index - 1) * 42}%; --preview-rotation: ${(index - 1) * 5}deg">
    `).join('') : '<span class="deck-tile-ball" aria-hidden="true"><i></i></span>';
    return `
      <article class="deck-tile">
        <div class="deck-tile-preview" aria-hidden="true">
          <div class="deck-tile-card-stack">${art}</div>
          <div class="deck-tile-total"><strong>${Number(summary.card_count || 0)}</strong><span>cards</span></div>
        </div>
        <div class="deck-tile-content">
          <p class="deck-tile-meta">${escapeHtml(format)} · ${ready ? 'ready to play' : 'in progress'}</p>
          <h3>${escapeHtml(summary.name)}</h3>
          <dl class="deck-tile-composition" aria-label="Deck composition">
            <div><dt>Pokémon</dt><dd>${counts.pokemon}</dd></div>
            <div><dt>Trainer</dt><dd>${counts.trainer}</dd></div>
            <div><dt>Energy</dt><dd>${counts.energy}</dd></div>
          </dl>
          <button class="deck-tile-action" type="button" data-export="${escapeHtml(summary.id)}">Open in builder</button>
        </div>
      </article>
    `;
  }

  window.TcgComponents = Object.freeze({ cardLineup, deckTile, featuredCards });
})();
