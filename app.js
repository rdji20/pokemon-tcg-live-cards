const state = {
  cards: [],
  filtered: [],
  manifest: null,
  page: 1,
  pageSize: 48,
};

const el = {
  headerCount: document.querySelector('#headerCount'),
  lastSync: document.querySelector('#lastSync'),
  syncButton: document.querySelector('#syncButton'),
  syncButtonLabel: document.querySelector('#syncButtonLabel'),
  totalCards: document.querySelector('#totalCards'),
  standardCards: document.querySelector('#standardCards'),
  totalSets: document.querySelector('#totalSets'),
  latestSet: document.querySelector('#latestSet'),
  latestDate: document.querySelector('#latestDate'),
  resultSummary: document.querySelector('#resultSummary'),
  search: document.querySelector('#searchInput'),
  set: document.querySelector('#setFilter'),
  type: document.querySelector('#typeFilter'),
  legality: document.querySelector('#legalityFilter'),
  sort: document.querySelector('#sortSelect'),
  loading: document.querySelector('#loadingState'),
  error: document.querySelector('#errorState'),
  errorMessage: document.querySelector('#errorMessage'),
  grid: document.querySelector('#cardGrid'),
  paginationTop: document.querySelector('#paginationTop'),
  pagination: document.querySelector('#pagination'),
  pagesTop: document.querySelector('#pageNumbersTop'),
  pages: document.querySelector('#pageNumbers'),
  previousTop: document.querySelector('#prevPageTop'),
  previous: document.querySelector('#prevPage'),
  nextTop: document.querySelector('#nextPageTop'),
  next: document.querySelector('#nextPage'),
  dialog: document.querySelector('#cardDialog'),
  dialogContent: document.querySelector('#dialogContent'),
  dialogClose: document.querySelector('.dialog-close'),
  sourceCommit: document.querySelector('#sourceCommit'),
};

const number = new Intl.NumberFormat('en-US');
const dateFormat = new Intl.DateTimeFormat('en-US', { year: 'numeric', month: 'short', day: 'numeric' });

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

function normalizedDate(value = '') {
  return value.replaceAll('/', '-');
}

function displayDate(value = '') {
  if (!value) return 'Unknown date';
  return dateFormat.format(new Date(`${normalizedDate(value)}T12:00:00`));
}

function collectorNumber(card) {
  const total = card.set?.printedTotal || card.set?.total;
  return total ? `${card.number}/${total}` : card.number || '—';
}

function prepareCard(card) {
  const moves = [...(card.attacks || []), ...(card.abilities || [])];
  card._search = [card.name, card.id, card.artist, card.rarity, card.set?.name, card.supertype,
    ...(card.subtypes || []), ...moves.flatMap(move => [move.name, move.text])]
    .filter(Boolean).join(' ').toLocaleLowerCase();
  return card;
}

async function loadCatalog(cacheKey = '') {
  try {
    el.error.hidden = true;
    const suffix = cacheKey ? `?v=${cacheKey}` : '';
    const [manifestResponse, cardsResponse] = await Promise.all([
      fetch(`data/manifest.json${suffix}`, { cache: 'no-store' }),
      fetch(`data/cards.jsonl${suffix}`, { cache: 'no-store' }),
    ]);
    if (!manifestResponse.ok || !cardsResponse.ok) throw new Error('The generated data files were not found.');

    state.manifest = await manifestResponse.json();
    const cardText = await cardsResponse.text();
    state.cards = cardText.trim().split('\n').filter(Boolean).map(line => prepareCard(JSON.parse(line)));
    populateSummary();
    populateSets();
    applyFilters();
    el.loading.hidden = true;
    el.grid.hidden = false;
    el.paginationTop.hidden = false;
    el.pagination.hidden = false;
  } catch (error) {
    el.loading.hidden = true;
    el.error.hidden = false;
    el.errorMessage.textContent = `${error.message} Start a local server in this folder, then open http://localhost:8000.`;
  }
}

function populateSummary() {
  const standard = state.cards.filter(card => card.catalog?.standardLegal).length;
  const sets = new Map(state.cards.map(card => [card.set.id, card.set]));
  const latest = [...sets.values()].sort((a, b) => normalizedDate(b.releaseDate).localeCompare(normalizedDate(a.releaseDate)))[0];
  el.headerCount.textContent = `${number.format(state.cards.length)} cards`;
  el.totalCards.textContent = number.format(state.cards.length);
  el.standardCards.textContent = number.format(standard);
  el.totalSets.textContent = number.format(sets.size);
  el.latestSet.textContent = latest?.name || '—';
  el.latestDate.textContent = displayDate(latest?.releaseDate);
  const commit = state.manifest?.source?.commit || '';
  el.sourceCommit.textContent = commit ? `source ${commit.slice(0, 12)}` : 'source unavailable';
  const generatedAt = state.manifest?.generatedAt;
  el.lastSync.textContent = generatedAt
    ? `Last sync: ${new Intl.DateTimeFormat('en-US', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(generatedAt))}`
    : 'Last sync: unknown';
}

function populateSets() {
  el.set.replaceChildren(new Option('All sets', ''));
  const sets = [...new Map(state.cards.map(card => [card.set.id, card.set])).values()]
    .sort((a, b) => normalizedDate(b.releaseDate).localeCompare(normalizedDate(a.releaseDate)) || a.name.localeCompare(b.name));
  const groups = new Map();
  for (const set of sets) {
    if (!groups.has(set.series)) groups.set(set.series, []);
    groups.get(set.series).push(set);
  }
  for (const [series, seriesSets] of groups) {
    const group = document.createElement('optgroup');
    group.label = series;
    for (const set of seriesSets) {
      const option = document.createElement('option');
      option.value = set.id;
      option.textContent = `${set.name} (${set.id.toUpperCase()})`;
      group.append(option);
    }
    el.set.append(group);
  }
}

function applyFilters(resetPage = true) {
  const query = el.search.value.trim().toLocaleLowerCase();
  const setId = el.set.value;
  const type = el.type.value;
  const legality = el.legality.value;

  state.filtered = state.cards.filter(card => {
    if (query && !card._search.includes(query)) return false;
    if (setId && card.set.id !== setId) return false;
    if (type && card.supertype !== type) return false;
    if (legality === 'standard' && !card.catalog.standardLegal) return false;
    if (legality === 'expanded' && !card.catalog.expandedLegal) return false;
    return true;
  });

  const sorters = {
    newest: (a, b) => normalizedDate(b.catalog.setReleaseDate).localeCompare(normalizedDate(a.catalog.setReleaseDate)) || a.name.localeCompare(b.name),
    oldest: (a, b) => normalizedDate(a.catalog.setReleaseDate).localeCompare(normalizedDate(b.catalog.setReleaseDate)) || a.name.localeCompare(b.name),
    name: (a, b) => a.name.localeCompare(b.name) || a.set.name.localeCompare(b.set.name),
    set: (a, b) => a.set.name.localeCompare(b.set.name) || String(a.number).localeCompare(String(b.number), undefined, { numeric: true }),
  };
  state.filtered.sort(sorters[el.sort.value]);
  if (resetPage) state.page = 1;
  render();
}

function render() {
  const totalPages = Math.max(1, Math.ceil(state.filtered.length / state.pageSize));
  state.page = Math.min(state.page, totalPages);
  const start = (state.page - 1) * state.pageSize;
  const cards = state.filtered.slice(start, start + state.pageSize);
  el.resultSummary.textContent = `${number.format(state.filtered.length)} ${state.filtered.length === 1 ? 'card' : 'cards'} found`;

  el.grid.innerHTML = cards.map((card, index) => `
    <article class="card-tile" tabindex="0" role="button" data-card-id="${escapeHtml(card.id)}" aria-label="Open ${escapeHtml(card.name)} details" style="animation-delay:${Math.min(index * 10, 180)}ms">
      <div class="card-image">
        <img src="${escapeHtml(card.images?.small)}" alt="${escapeHtml(card.name)} card" loading="lazy">
        <div class="card-badges">
          <span class="badge">Live</span>
          ${card.catalog.standardLegal ? '<span class="badge standard">Standard</span>' : ''}
        </div>
      </div>
      <div class="card-info">
        <strong title="${escapeHtml(card.name)}">${escapeHtml(card.name)}</strong>
        <p><span title="${escapeHtml(card.set.name)}">${escapeHtml(card.set.name)}</span><span>${escapeHtml(collectorNumber(card))}</span></p>
      </div>
    </article>
  `).join('');

  el.previous.disabled = state.page === 1;
  el.previousTop.disabled = state.page === 1;
  el.next.disabled = state.page === totalPages;
  el.nextTop.disabled = state.page === totalPages;
  renderPageNumbers(el.pagesTop, totalPages);
  renderPageNumbers(el.pages, totalPages);
}

function renderPageNumbers(container, totalPages) {
  const candidates = new Set([1, totalPages, state.page - 1, state.page, state.page + 1]);
  const pages = [...candidates].filter(page => page > 0 && page <= totalPages).sort((a, b) => a - b);
  let previous = 0;
  container.innerHTML = pages.map(page => {
    const gap = page - previous > 1 ? '<span aria-hidden="true">…</span>' : '';
    previous = page;
    return `${gap}<button type="button" data-page="${page}" class="${page === state.page ? 'active' : ''}" aria-label="Page ${page}" ${page === state.page ? 'aria-current="page"' : ''}>${page}</button>`;
  }).join('');
}

function openCard(card) {
  const abilities = (card.abilities || []).map(ability => `
    <div class="ability"><strong>${escapeHtml(ability.name)}</strong><p>${escapeHtml(ability.text)}</p></div>
  `).join('');
  const attacks = (card.attacks || []).map(attack => `
    <div class="move"><div class="move-line"><span>${escapeHtml(attack.name)}</span><span>${escapeHtml(attack.damage || '')}</span></div><p>${escapeHtml(attack.text || '')}</p></div>
  `).join('');
  const tags = [card.supertype, ...(card.subtypes || []), ...(card.types || []), card.catalog.standardLegal ? 'Standard' : null, card.catalog.expandedLegal ? 'Expanded' : null].filter(Boolean);
  el.dialogContent.innerHTML = `
    <div class="dialog-layout">
      <div class="dialog-art">
        <div class="interactive-card" id="interactiveCard" aria-label="Drag to tilt the card">
          <img src="${escapeHtml(card.images?.large || card.images?.small)}" alt="${escapeHtml(card.name)} card" draggable="false">
          <span class="card-shine" aria-hidden="true"></span>
        </div>
      </div>
      <div class="dialog-details">
        <span class="dialog-kicker">${escapeHtml(card.set.series)} · ${escapeHtml(card.set.id)}</span>
        <h3 id="dialogTitle">${escapeHtml(card.name)}</h3>
        <p class="dialog-subtitle">${escapeHtml(card.set.name)} · ${escapeHtml(collectorNumber(card))}</p>
        <div class="dialog-tags">${tags.map(tag => `<span>${escapeHtml(tag)}</span>`).join('')}</div>
        ${abilities || attacks ? `<section class="detail-section"><h4>Card text</h4>${abilities}${attacks}</section>` : ''}
        <section class="detail-section detail-grid">
          <div><span>Rarity</span><strong>${escapeHtml(card.rarity || '—')}</strong></div>
          <div><span>HP</span><strong>${escapeHtml(card.hp || '—')}</strong></div>
          <div><span>Illustrator</span><strong>${escapeHtml(card.artist || '—')}</strong></div>
          <div><span>Released</span><strong>${escapeHtml(displayDate(card.catalog.setReleaseDate))}</strong></div>
          <div><span>Card ID</span><strong>${escapeHtml(card.id)}</strong></div>
          <div><span>Regulation</span><strong>${escapeHtml(card.regulationMark || '—')}</strong></div>
        </section>
      </div>
    </div>`;
  el.dialog.showModal();
  initializeCardTilt();
}

function initializeCardTilt() {
  const card = document.querySelector('#interactiveCard');
  if (!card) return;

  let activePointer = null;
  let startX = 0;
  let startY = 0;

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
  const reset = () => {
    activePointer = null;
    card.classList.remove('is-dragging');
    card.style.setProperty('--move-x', '0px');
    card.style.setProperty('--move-y', '0px');
    card.style.setProperty('--rotate-x', '0deg');
    card.style.setProperty('--rotate-y', '0deg');
    card.style.setProperty('--card-scale', '1');
    card.style.setProperty('--shine-opacity', '0');
  };

  card.addEventListener('pointerdown', event => {
    if (event.button !== 0 && event.pointerType === 'mouse') return;
    activePointer = event.pointerId;
    startX = event.clientX;
    startY = event.clientY;
    card.setPointerCapture(event.pointerId);
    card.classList.add('is-dragging');
    card.style.setProperty('--card-scale', '1.025');
    card.style.setProperty('--shine-opacity', '.75');
  });

  card.addEventListener('pointermove', event => {
    if (event.pointerId !== activePointer) return;
    const rect = card.getBoundingClientRect();
    const deltaX = event.clientX - startX;
    const deltaY = event.clientY - startY;
    const rotateY = clamp((deltaX / rect.width) * 38, -20, 20);
    const rotateX = clamp((-deltaY / rect.height) * 38, -20, 20);
    const moveX = clamp(deltaX * .07, -14, 14);
    const moveY = clamp(deltaY * .05, -10, 10);
    const shineX = clamp(((event.clientX - rect.left) / rect.width) * 100, 0, 100);
    const shineY = clamp(((event.clientY - rect.top) / rect.height) * 100, 0, 100);

    card.style.setProperty('--move-x', `${moveX}px`);
    card.style.setProperty('--move-y', `${moveY}px`);
    card.style.setProperty('--rotate-x', `${rotateX}deg`);
    card.style.setProperty('--rotate-y', `${rotateY}deg`);
    card.style.setProperty('--shine-x', `${shineX}%`);
    card.style.setProperty('--shine-y', `${shineY}%`);
  });

  card.addEventListener('pointerup', event => {
    if (event.pointerId !== activePointer) return;
    card.releasePointerCapture(event.pointerId);
    reset();
  });
  card.addEventListener('pointercancel', reset);
  card.addEventListener('lostpointercapture', () => {
    if (activePointer !== null) reset();
  });
}

let searchTimer;
el.search.addEventListener('input', () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => applyFilters(), 120);
});
[el.set, el.type, el.legality, el.sort].forEach(control => control.addEventListener('change', () => applyFilters()));
el.previous.addEventListener('click', () => changePage(state.page - 1));
el.previousTop.addEventListener('click', () => changePage(state.page - 1));
el.next.addEventListener('click', () => changePage(state.page + 1));
el.nextTop.addEventListener('click', () => changePage(state.page + 1));
[el.pagesTop, el.pages].forEach(container => {
  container.addEventListener('click', event => {
    const button = event.target.closest('[data-page]');
    if (button) changePage(Number(button.dataset.page));
  });
});

function changePage(page) {
  state.page = page;
  render();
  document.querySelector('.explorer').scrollIntoView({ behavior: 'smooth' });
}

el.grid.addEventListener('click', event => {
  const tile = event.target.closest('[data-card-id]');
  if (tile) openCard(state.cards.find(card => card.id === tile.dataset.cardId));
});
el.grid.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    const tile = event.target.closest('[data-card-id]');
    if (tile) { event.preventDefault(); openCard(state.cards.find(card => card.id === tile.dataset.cardId)); }
  }
});
el.dialogClose.addEventListener('click', () => el.dialog.close());
el.dialog.addEventListener('click', event => {
  if (event.target === el.dialog) el.dialog.close();
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && document.activeElement !== el.search) {
    event.preventDefault();
    el.search.focus();
  }
});

el.syncButton.addEventListener('click', async () => {
  const originalLabel = el.syncButtonLabel.textContent;
  el.syncButton.disabled = true;
  el.syncButtonLabel.textContent = 'Syncing…';
  el.lastSync.textContent = 'Sync in progress…';
  try {
    const response = await fetch('/api/sync', {
      method: 'POST',
      headers: { 'X-PTCGL-Action': 'sync' },
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Sync failed');
    await loadCatalog(Date.now());
    el.syncButtonLabel.textContent = 'Synced';
    setTimeout(() => { el.syncButtonLabel.textContent = originalLabel; }, 1600);
  } catch (error) {
    el.lastSync.textContent = `Sync failed: ${error.message}`;
    el.syncButtonLabel.textContent = 'Try again';
  } finally {
    el.syncButton.disabled = false;
  }
});

loadCatalog();
