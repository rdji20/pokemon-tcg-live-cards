const state = {
  decks: [],
  deckDetails: new Map(),
  draft: new Map(),
  library: [],
  libraryPage: 1,
  libraryPages: 1,
  libraryTotal: 0,
  libraryLoading: false,
  libraryRequest: 0,
  supertype: '',
  deckType: '',
  activePrintGroup: '',
  activePrints: [],
};

const ids = [
  'deckCount', 'deckFormat', 'deckName', 'decklist', 'validateButton', 'saveButton',
  'newDeckButton', 'validationResult', 'energyType', 'optimizerSeed', 'optimizeButton',
  'optimizerResult', 'refreshDecksButton', 'savedDecks', 'cardSearch', 'setFilter',
  'sortFilter', 'cardTypeTabs', 'cardLibrary', 'libraryResultCount', 'loadMoreCards',
  'deckCardList', 'totalCount', 'allCount', 'pokemonCount', 'trainerCount',
  'energyCount', 'deckReadiness', 'deckProgress', 'importButton',
  'printDialog', 'printDialogTitle', 'printPickerGrid', 'closePrintDialog',
];
const el = Object.fromEntries(ids.map(id => [id, document.getElementById(id)]));

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

function draftCard(card, quantity = 1) {
  return {
    card_id: card.card_id || card.id,
    quantity: Number(quantity || card.quantity || 1),
    name: card.name || 'Unknown card',
    supertype: card.supertype || '',
    subtypes: card.subtypes || [],
    image_small: card.image_small || card.images?.small || card.image_large || card.images?.large || '',
    image_large: card.image_large || card.images?.large || card.image_small || card.images?.small || '',
    set_id: card.set_id || card.set?.id || '',
    set_name: card.set_name || card.set?.name || card.set_id || card.set?.id || '',
    number: card.number || '',
    print_group: card.print_group || card.printGroup || null,
  };
}

function draftEntries() {
  return [...state.draft.values()].map(card => ({ card_id: card.card_id, quantity: card.quantity }));
}

function draftPayload() {
  return {
    name: el.deckName.value.trim() || 'Untitled deck',
    format: 'standard',
    cards: draftEntries(),
  };
}

function isBasicEnergy(card) {
  return card.supertype === 'Energy' && (card.subtypes || []).includes('Basic');
}

function totalCards() {
  return [...state.draft.values()].reduce((total, card) => total + card.quantity, 0);
}

function quantityForName(name) {
  return [...state.draft.values()]
    .filter(card => card.name === name && !isBasicEnergy(card))
    .reduce((total, card) => total + card.quantity, 0);
}

function quantityForPrintGroup(printGroup, fallbackId = '') {
  return [...state.draft.values()]
    .filter(card => card.print_group ? card.print_group === printGroup : card.card_id === fallbackId)
    .reduce((total, card) => total + card.quantity, 0);
}

function composition() {
  return [...state.draft.values()].reduce((counts, card) => {
    const key = card.supertype === 'Pokémon' ? 'pokemon' : String(card.supertype || '').toLowerCase();
    if (key in counts) counts[key] += card.quantity;
    return counts;
  }, { pokemon: 0, trainer: 0, energy: 0 });
}

function setFeedback(message, kind = '') {
  el.validationResult.className = `deck-feedback${kind ? ` ${kind}` : ''}`;
  el.validationResult.innerHTML = message;
}

function showValidation(result) {
  const target = result.validation || result;
  const errors = target.errors || [];
  if (target.valid) {
    setFeedback(`<strong>Valid Standard deck.</strong> ${target.cardCount} cards across ${target.uniquePrints} prints.`, 'success');
    return;
  }
  const visible = errors.slice(0, 4);
  setFeedback(
    `<strong>${errors.length} issue${errors.length === 1 ? '' : 's'} to fix.</strong>` +
    `<ul>${visible.map(error => `<li>${escapeHtml(error.message)}</li>`).join('')}</ul>` +
    (errors.length > visible.length ? `<span>${errors.length - visible.length} more</span>` : ''),
    'error'
  );
}

function cardPrint(card) {
  return [card.set_name || card.set_id, card.number].filter(Boolean).join(' · ');
}

function renderDeck() {
  const cards = [...state.draft.values()].sort((left, right) => {
    const order = { 'Pokémon': 0, Trainer: 1, Energy: 2 };
    return (order[left.supertype] ?? 3) - (order[right.supertype] ?? 3) || left.name.localeCompare(right.name);
  });
  const filtered = state.deckType ? cards.filter(card => card.supertype === state.deckType) : cards;
  const counts = composition();
  const total = totalCards();

  el.totalCount.textContent = total;
  el.allCount.textContent = total;
  el.pokemonCount.textContent = counts.pokemon;
  el.trainerCount.textContent = counts.trainer;
  el.energyCount.textContent = counts.energy;
  el.deckProgress.style.width = `${Math.min(100, total / 60 * 100)}%`;
  el.deckReadiness.textContent = total === 60 ? 'Ready to check' : total > 60 ? `${total - 60} over` : `${60 - total} remaining`;
  el.deckReadiness.className = `deck-readiness${total === 60 ? ' ready' : total > 60 ? ' over' : ''}`;

  if (!cards.length) {
    el.deckCardList.innerHTML = `
      <div class="empty-deck">
        <span class="empty-deck-ball" aria-hidden="true"><i></i></span>
        <strong>Your deck starts empty</strong>
        <p>Choose cards from the library. You can change quantities here at any time.</p>
      </div>`;
    return;
  }
  if (!filtered.length) {
    el.deckCardList.innerHTML = '<div class="empty-deck"><strong>No cards in this section yet</strong><p>Choose another section or add cards from the library.</p></div>';
    return;
  }
  el.deckCardList.innerHTML = filtered.map(card => `
    <article class="deck-card-row" data-card-id="${escapeHtml(card.card_id)}">
      <img src="${escapeHtml(card.image_small || card.image_large)}" alt="" loading="lazy">
      <div class="deck-card-copy">
        <strong>${escapeHtml(card.name)}</strong>
        <span>${escapeHtml(card.supertype)} · ${escapeHtml(cardPrint(card))}</span>
      </div>
      <div class="deck-row-controls" aria-label="${escapeHtml(card.name)} quantity">
        <button type="button" data-change="-1" aria-label="Remove one ${escapeHtml(card.name)}">−</button>
        <strong>${card.quantity}</strong>
        <button type="button" data-change="1" aria-label="Add one ${escapeHtml(card.name)}">+</button>
      </div>
    </article>`).join('');
}

function libraryCardMarkup(card) {
  const draft = state.draft.get(card.id);
  const printCount = Number(card.printCount || 1);
  const groupedQuantity = quantityForPrintGroup(card.printGroup, card.id);
  const quantity = printCount > 1 ? groupedQuantity : (draft?.quantity || 0);
  const atNameLimit = !isBasicEnergy(card) && quantityForName(card.name) >= 4;
  const full = totalCards() >= 60;
  const setName = card.set?.name || card.set?.id || '';
  const pickerLabel = `${printCount} print${printCount === 1 ? '' : 's'}${quantity ? ` · ${quantity} in deck` : ''}`;
  return `
    <article class="library-card" data-card-id="${escapeHtml(card.id)}">
      <button class="library-card-art" type="button" ${printCount > 1 ? `data-choose-prints="${escapeHtml(card.printGroup)}"` : 'data-add-card'} aria-label="${printCount > 1 ? 'Choose a print of' : 'Add'} ${escapeHtml(card.name)}${printCount > 1 ? '' : ' to deck'}" ${printCount === 1 && (atNameLimit || full) ? 'disabled' : ''}>
        <img src="${escapeHtml(card.images?.small || card.images?.large || '')}" alt="${escapeHtml(card.name)} card" loading="lazy">
      </button>
      <p class="library-card-name" title="${escapeHtml(card.name)}">${escapeHtml(card.name)}</p>
      <p class="library-card-print" title="${escapeHtml(setName)}">${escapeHtml(setName)} · ${escapeHtml(card.number || '')}</p>
      <div class="library-card-controls">
        ${printCount > 1 ? `
          <button class="choose-print-button" type="button" data-choose-prints="${escapeHtml(card.printGroup)}">${escapeHtml(pickerLabel)}</button>` : quantity ? `
          <div class="library-quantity" aria-label="${escapeHtml(card.name)} quantity">
            <button type="button" data-library-change="-1" aria-label="Remove one ${escapeHtml(card.name)}">−</button>
            <strong>${quantity} in deck</strong>
            <button type="button" data-library-change="1" aria-label="Add one ${escapeHtml(card.name)}" ${atNameLimit || full ? 'disabled' : ''}>+</button>
          </div>` : `
          <button class="add-card-button" type="button" data-add-card ${atNameLimit || full ? 'disabled' : ''}>${full ? 'Deck full' : atNameLimit ? '4-copy limit' : 'Add to deck'}</button>`}
      </div>
      ${atNameLimit && !quantity ? '<p class="library-card-copy-limit">Another print already fills the 4-copy limit</p>' : ''}
    </article>`;
}

function renderLibrary() {
  if (!state.library.length && state.libraryLoading) {
    el.cardLibrary.innerHTML = '<div class="library-loading">Loading the Standard card library…</div>';
    return;
  }
  if (!state.library.length) {
    el.cardLibrary.innerHTML = '<div class="library-empty">No Standard cards match these filters.</div>';
    return;
  }
  el.cardLibrary.innerHTML = state.library.map(libraryCardMarkup).join('');
}

function renderPrintPicker() {
  if (!state.activePrints.length) {
    el.printPickerGrid.innerHTML = '<div class="print-picker-message">Loading available prints…</div>';
    return;
  }
  el.printPickerGrid.innerHTML = state.activePrints.map(card => {
    const draft = state.draft.get(card.id);
    const quantity = draft?.quantity || 0;
    const atNameLimit = !isBasicEnergy(card) && quantityForName(card.name) >= 4;
    const full = totalCards() >= 60;
    const setName = card.set?.name || card.set?.id || '';
    return `
      <article class="print-option" data-card-id="${escapeHtml(card.id)}">
        <img src="${escapeHtml(card.images?.small || card.images?.large || '')}" alt="${escapeHtml(card.name)} from ${escapeHtml(setName)}" loading="lazy">
        <div class="print-option-copy">
          <strong>${escapeHtml(setName)}</strong>
          <span>${escapeHtml(card.number || '')}${card.rarity ? ` · ${escapeHtml(card.rarity)}` : ''}</span>
        </div>
        ${quantity ? `
          <div class="print-option-quantity" aria-label="${escapeHtml(card.name)} quantity from ${escapeHtml(setName)}">
            <button type="button" data-print-change="-1" aria-label="Remove one">−</button>
            <strong>${quantity} in deck</strong>
            <button type="button" data-print-change="1" aria-label="Add one" ${atNameLimit || full ? 'disabled' : ''}>+</button>
          </div>` : `
          <button class="select-print-button" type="button" data-print-change="1" ${atNameLimit || full ? 'disabled' : ''}>${full ? 'Deck full' : atNameLimit ? '4-copy limit reached' : 'Add this print'}</button>`}
      </article>`;
  }).join('');
}

async function openPrintPicker(printGroup, cardName) {
  state.activePrintGroup = printGroup;
  state.activePrints = [];
  el.printDialogTitle.textContent = cardName || 'Available prints';
  renderPrintPicker();
  if (!el.printDialog.open) el.printDialog.showModal();
  try {
    const result = await api(`/api/card-prints?group=${encodeURIComponent(printGroup)}&legality=standard`);
    if (state.activePrintGroup !== printGroup) return;
    state.activePrints = result.items.map(card => ({ ...card, printGroup }));
    renderPrintPicker();
  } catch (error) {
    el.printPickerGrid.innerHTML = `<div class="print-picker-message">${escapeHtml(error.message)}</div>`;
  }
}

function changeCardQuantity(cardId, change, sourceCard = null) {
  const existing = state.draft.get(cardId);
  const card = existing || (sourceCard ? draftCard(sourceCard, 0) : null);
  if (!card) return;
  const next = (existing?.quantity || 0) + change;

  if (change > 0) {
    if (totalCards() >= 60) {
      setFeedback('The deck already has 60 cards. Remove one before adding another.', 'error');
      return;
    }
    if (!isBasicEnergy(card) && quantityForName(card.name) >= 4) {
      setFeedback(`${escapeHtml(card.name)} is already at the 4-copy limit across all prints.`, 'error');
      return;
    }
  }
  if (next <= 0) state.draft.delete(cardId);
  else state.draft.set(cardId, { ...card, quantity: next });
  setFeedback(totalCards() ? 'Deck changed. Check it when you reach 60 cards.' : 'Add cards to begin.');
  renderDeck();
  renderLibrary();
}

async function loadCards({ append = false } = {}) {
  state.libraryLoading = true;
  const request = ++state.libraryRequest;
  if (!append) {
    state.library = [];
    state.libraryPage = 1;
  }
  renderLibrary();
  el.loadMoreCards.hidden = true;
  const params = new URLSearchParams({
    legality: 'standard',
    page: String(state.libraryPage),
    page_size: '60',
    sort: el.sortFilter.value,
    grouped: 'true',
  });
  const query = el.cardSearch.value.trim();
  if (query) params.set('q', query);
  if (el.setFilter.value) params.set('set_id', el.setFilter.value);
  if (state.supertype) params.set('supertype', state.supertype);
  try {
    const result = await api(`/api/cards?${params}`);
    if (request !== state.libraryRequest) return;
    state.library = append ? [...state.library, ...result.items] : result.items;
    state.libraryTotal = result.total;
    state.libraryPages = result.pages;
    const shown = state.library.length;
    el.libraryResultCount.textContent = `${result.total.toLocaleString()} unique card${result.total === 1 ? '' : 's'} · ${result.printTotal.toLocaleString()} prints · showing ${shown.toLocaleString()}`;
    el.loadMoreCards.hidden = state.libraryPage >= state.libraryPages;
  } catch (error) {
    if (request === state.libraryRequest) {
      el.cardLibrary.innerHTML = `<div class="library-empty">${escapeHtml(error.message)}</div>`;
      el.libraryResultCount.textContent = 'Could not load cards';
    }
  } finally {
    if (request === state.libraryRequest) {
      state.libraryLoading = false;
      renderLibrary();
    }
  }
}

async function loadSets() {
  try {
    const result = await api('/api/sets');
    for (const set of result.items) {
      el.setFilter.add(new Option(`${set.name} (${set.card_count})`, set.id));
    }
  } catch (error) {
    el.setFilter.add(new Option('Sets unavailable', '', true, true));
    el.setFilter.disabled = true;
  }
}

function startBlankDeck({ focus = false } = {}) {
  state.draft.clear();
  state.deckType = '';
  el.deckName.value = 'Untitled deck';
  el.deckFormat.value = 'standard';
  el.decklist.value = '';
  document.querySelectorAll('[data-deck-type]').forEach(button => {
    button.classList.toggle('active', button.dataset.deckType === '');
  });
  setFeedback('Blank Standard deck ready. Add cards from the library.');
  renderDeck();
  renderLibrary();
  if (focus) {
    el.deckName.focus();
    el.deckName.select();
  }
}

function applyDraftCards(cards) {
  state.draft.clear();
  for (const source of cards || []) {
    const card = draftCard(source, source.quantity);
    if (card.card_id && card.quantity > 0) state.draft.set(card.card_id, card);
  }
  renderDeck();
  renderLibrary();
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
    state.deckDetails.set(result.deck.id, result.deck);
    await loadDecks();
  } catch (error) {
    showValidation(error.payload || { valid: false, errors: [{ message: error.message }] });
  }
}

async function importDecklist() {
  if (!el.decklist.value.trim()) {
    setFeedback('Paste a Pokémon TCG Live deck list first.', 'error');
    return;
  }
  const payload = { name: el.deckName.value.trim() || 'Imported deck', format: 'standard', decklist: el.decklist.value };
  try {
    const result = await api('/api/decks/validate', { method: 'POST', body: JSON.stringify(payload) });
    applyDraftCards(result.cards);
    showValidation(result);
  } catch (error) {
    const result = error.payload;
    if (result?.cards?.length) applyDraftCards(result.cards);
    showValidation(result || { valid: false, errors: [{ message: error.message }] });
  }
}

async function optimize() {
  el.optimizerResult.textContent = 'Creating a starting list…';
  try {
    const result = await api('/api/optimize', {
      method: 'POST',
      body: JSON.stringify({ format: 'standard', type: el.energyType.value, seed: Number(el.optimizerSeed.value || 1) }),
    });
    applyDraftCards(result.cards);
    el.optimizerResult.textContent = `${result.cardCount}-card ${result.algorithmVersion} draft loaded. Tune it before saving.`;
    setFeedback('Quick Build created a draft. Check the card choices and legality before saving.');
  } catch (error) {
    el.optimizerResult.textContent = error.message;
  }
}

async function loadDeckDetail(id) {
  if (!state.deckDetails.has(id)) {
    const result = await api(`/api/decks/${id}`);
    state.deckDetails.set(id, result.deck);
  }
  return state.deckDetails.get(id);
}

async function openSavedDeck(id) {
  try {
    const deck = await loadDeckDetail(id);
    applyDraftCards(deck.cards);
    el.deckName.value = deck.name;
    const exported = await api(`/api/decks/${id}/export`);
    el.decklist.value = exported.decklist;
    setFeedback(`Loaded ${escapeHtml(deck.name)}. Changes stay in the builder until you save them.`, 'success');
    window.scrollTo({ top: 0, behavior: 'smooth' });
  } catch (error) {
    setFeedback(escapeHtml(error.message), 'error');
  }
}

async function loadDecks() {
  try {
    const result = await api('/api/decks');
    state.decks = result.items;
    el.deckCount.textContent = `${state.decks.length} saved deck${state.decks.length === 1 ? '' : 's'}`;
    if (!state.decks.length) {
      el.savedDecks.innerHTML = '<p class="empty-copy">No saved decks yet.</p>';
      return;
    }
    el.savedDecks.innerHTML = '<p class="empty-copy">Opening deck boxes…</p>';
    const details = await Promise.all(state.decks.map(deck => loadDeckDetail(deck.id).catch(() => null)));
    el.savedDecks.innerHTML = state.decks.map((deck, index) => window.TcgComponents.deckTile(deck, details[index])).join('');
  } catch (error) {
    el.savedDecks.innerHTML = `<p class="empty-copy">${escapeHtml(error.message)}</p>`;
  }
}

let searchTimer = 0;
el.cardSearch.addEventListener('input', () => {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => loadCards(), 240);
});
el.setFilter.addEventListener('change', () => loadCards());
el.sortFilter.addEventListener('change', () => loadCards());
el.cardTypeTabs.addEventListener('click', event => {
  const button = event.target.closest('[data-supertype]');
  if (!button) return;
  state.supertype = button.dataset.supertype;
  el.cardTypeTabs.querySelectorAll('[data-supertype]').forEach(tab => tab.setAttribute('aria-selected', String(tab === button)));
  loadCards();
});
el.cardLibrary.addEventListener('click', event => {
  const article = event.target.closest('[data-card-id]');
  if (!article) return;
  const card = state.library.find(item => item.id === article.dataset.cardId);
  if (!card) return;
  const printPicker = event.target.closest('[data-choose-prints]');
  if (printPicker) {
    openPrintPicker(printPicker.dataset.choosePrints, card.name);
  } else if (event.target.closest('[data-library-change]')) {
    changeCardQuantity(card.id, Number(event.target.closest('[data-library-change]').dataset.libraryChange), card);
  } else if (event.target.closest('[data-add-card]')) {
    changeCardQuantity(card.id, 1, card);
  }
});
el.printPickerGrid.addEventListener('click', event => {
  const button = event.target.closest('[data-print-change]');
  const article = event.target.closest('[data-card-id]');
  if (!button || !article) return;
  const card = state.activePrints.find(item => item.id === article.dataset.cardId);
  if (!card) return;
  changeCardQuantity(card.id, Number(button.dataset.printChange), card);
  renderPrintPicker();
});
el.closePrintDialog.addEventListener('click', () => el.printDialog.close());
el.printDialog.addEventListener('click', event => {
  if (event.target === el.printDialog) el.printDialog.close();
});
el.deckCardList.addEventListener('click', event => {
  const button = event.target.closest('[data-change]');
  const row = event.target.closest('[data-card-id]');
  if (button && row) changeCardQuantity(row.dataset.cardId, Number(button.dataset.change));
});
document.querySelector('.composition-tabs').addEventListener('click', event => {
  const button = event.target.closest('[data-deck-type]');
  if (!button) return;
  state.deckType = button.dataset.deckType;
  document.querySelectorAll('[data-deck-type]').forEach(tab => tab.classList.toggle('active', tab === button));
  renderDeck();
});
el.loadMoreCards.addEventListener('click', () => {
  if (state.libraryPage < state.libraryPages) {
    state.libraryPage += 1;
    loadCards({ append: true });
  }
});
el.newDeckButton.addEventListener('click', () => startBlankDeck({ focus: true }));
el.validateButton.addEventListener('click', validateDeck);
el.saveButton.addEventListener('click', saveDeck);
el.importButton.addEventListener('click', importDecklist);
el.optimizeButton.addEventListener('click', optimize);
el.refreshDecksButton.addEventListener('click', loadDecks);
el.savedDecks.addEventListener('click', event => {
  const button = event.target.closest('[data-export]');
  if (button) openSavedDeck(button.dataset.export);
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    event.preventDefault();
    el.cardSearch.focus();
  }
});

startBlankDeck();
Promise.all([loadSets(), loadCards(), loadDecks()]);
