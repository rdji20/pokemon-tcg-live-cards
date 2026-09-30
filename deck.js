const state = { decks: [], generatedCards: null };

const el = Object.fromEntries([
  'deckCount', 'deckFormat', 'deckName', 'decklist', 'validateButton', 'saveButton',
  'validationResult', 'energyType', 'optimizerSeed', 'optimizeButton', 'optimizerResult',
  'refreshDecksButton', 'savedDecks', 'deckA', 'deckB', 'games', 'simulationSeed',
  'policyA', 'policyB', 'simulateButton', 'simulationResult'
].map(id => [id, document.getElementById(id)]));

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.payload = payload;
    throw error;
  }
  return payload;
}

function draftPayload() {
  const payload = { name: el.deckName.value.trim() || 'Untitled deck', format: el.deckFormat.value };
  if (state.generatedCards) payload.cards = state.generatedCards;
  else payload.decklist = el.decklist.value;
  return payload;
}

function showValidation(result) {
  const target = result.validation || result;
  const errors = target.errors || [];
  el.validationResult.className = `result-box ${target.valid ? 'success' : 'error'}`;
  el.validationResult.innerHTML = target.valid
    ? `<strong>Valid ${escapeHtml(target.format)} deck.</strong> ${target.cardCount} cards across ${target.uniquePrints} prints.`
    : `<strong>${errors.length} problem${errors.length === 1 ? '' : 's'}.</strong><ul>${errors.map(error => `<li>${escapeHtml(error.message)}</li>`).join('')}</ul>`;
}

async function validateDeck() {
  try {
    showValidation(await api('/api/decks/validate', { method: 'POST', body: JSON.stringify(draftPayload()) }));
  } catch (error) {
    showValidation(error.payload || { valid: false, errors: [{ message: error.message }] });
  }
}

async function saveDeck() {
  try {
    const result = await api('/api/decks', { method: 'POST', body: JSON.stringify(draftPayload()) });
    showValidation(result);
    state.generatedCards = null;
    await loadDecks();
  } catch (error) {
    showValidation(error.payload || { valid: false, errors: [{ message: error.message }] });
  }
}

async function optimize() {
  el.optimizerResult.className = 'result-box';
  el.optimizerResult.textContent = 'Generating…';
  try {
    const result = await api('/api/optimize', {
      method: 'POST',
      body: JSON.stringify({
        format: el.deckFormat.value === 'unlimited' ? 'standard' : el.deckFormat.value,
        type: el.energyType.value,
        seed: Number(el.optimizerSeed.value || 1),
      }),
    });
    state.generatedCards = result.cards.map(card => ({ card_id: card.card_id, quantity: card.quantity }));
    el.decklist.value = result.cards.map(card => `${card.quantity} × ${card.name}`).join('\n');
    el.optimizerResult.className = 'result-box success';
    el.optimizerResult.innerHTML = `<strong>${result.cardCount}-card draft ready.</strong> ${result.cards.length} unique prints using ${escapeHtml(result.algorithmVersion)}. Validate it, name it, then save.`;
    await validateDeck();
  } catch (error) {
    el.optimizerResult.className = 'result-box error';
    el.optimizerResult.textContent = error.message;
  }
}

async function exportDeck(id) {
  try {
    const result = await api(`/api/decks/${id}/export`);
    el.decklist.value = result.decklist;
    state.generatedCards = null;
    el.decklist.focus();
    await navigator.clipboard?.writeText(result.decklist);
    el.validationResult.className = 'result-box success';
    el.validationResult.textContent = 'Deck list loaded and copied to the clipboard.';
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (error) {
    el.validationResult.className = 'result-box error';
    el.validationResult.textContent = error.message;
  }
}

function populateDeckSelect(select, selectedId) {
  select.replaceChildren();
  for (const deck of state.decks) {
    const option = new Option(`${deck.name} (${deck.card_count})`, deck.id);
    option.selected = deck.id === selectedId;
    select.add(option);
  }
}

async function loadDecks() {
  try {
    const result = await api('/api/decks');
    const previousA = el.deckA.value;
    const previousB = el.deckB.value;
    state.decks = result.items;
    el.deckCount.textContent = `${state.decks.length} saved deck${state.decks.length === 1 ? '' : 's'}`;
    el.savedDecks.innerHTML = state.decks.length ? state.decks.map(deck => `
      <article class="saved-deck">
        <strong>${escapeHtml(deck.name)}</strong>
        <p>${escapeHtml(deck.format)} · ${deck.card_count} cards</p>
        <div class="saved-deck-actions"><button type="button" data-export="${deck.id}">Export</button></div>
      </article>
    `).join('') : '<p class="empty-copy">No saved decks.</p>';
    populateDeckSelect(el.deckA, previousA || state.decks[0]?.id);
    populateDeckSelect(el.deckB, previousB || state.decks[1]?.id || state.decks[0]?.id);
    el.simulateButton.disabled = !state.decks.length;
  } catch (error) {
    el.savedDecks.innerHTML = `<p class="empty-copy">${escapeHtml(error.message)}</p>`;
  }
}

async function simulate() {
  el.simulationResult.className = 'result-box';
  el.simulationResult.textContent = 'Running…';
  try {
    const result = await api('/api/simulations', {
      method: 'POST',
      body: JSON.stringify({
        deckA: el.deckA.value, deckB: el.deckB.value,
        games: Number(el.games.value || 100), seed: Number(el.simulationSeed.value || 1),
        policyA: el.policyA.value, policyB: el.policyB.value,
      }),
    });
    el.simulationResult.className = 'result-box success';
    el.simulationResult.innerHTML = `<strong>${result.games} games complete.</strong> Deck A: ${result.deckAWins} wins · Deck B: ${result.deckBWins} wins · Draws: ${result.draws} · Average: ${result.averageTurns.toFixed(1)} turns.`;
  } catch (error) {
    el.simulationResult.className = 'result-box error';
    el.simulationResult.textContent = error.message;
  }
}

el.decklist.addEventListener('input', () => { state.generatedCards = null; });
el.validateButton.addEventListener('click', validateDeck);
el.saveButton.addEventListener('click', saveDeck);
el.optimizeButton.addEventListener('click', optimize);
el.refreshDecksButton.addEventListener('click', loadDecks);
el.simulateButton.addEventListener('click', simulate);
el.savedDecks.addEventListener('click', event => {
  const button = event.target.closest('[data-export]');
  if (button) exportDeck(button.dataset.export);
});

loadDecks();
