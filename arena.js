const arenaState = { game: null, busy: false, selectedCardUid: null };

const arenaEl = Object.fromEntries([
  'arenaStatus', 'arenaLobby', 'startMatchForm', 'playerDeck', 'opponentType',
  'arenaSeed', 'startMatchButton', 'lobbyMessage', 'gameShell', 'turnStatus',
  'turnNumber', 'newMatchButton', 'gameTable', 'fullscreenButton', 'fullscreenLabel',
  'battlePhase', 'setupOverlay', 'setupCoin', 'setupStep', 'setupTitle',
  'setupText', 'setupActionList', 'setupFacts', 'opponentName', 'opponentCounts', 'opponentHand',
  'opponentBench', 'opponentPrizes', 'opponentActive', 'opponentDeck',
  'playerName', 'playerCounts', 'playerBench', 'playerPrizes', 'playerActive',
  'playerDeckStack', 'playerHand', 'handCount', 'battleMessage', 'actionList',
  'battleLog', 'arenaLimitations'
].map(id => [id, document.getElementById(id)]));

function escapeHtml(value = '') {
  return String(value).replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[character]);
}

async function arenaApi(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function prizeCards(count) {
  return Array.from({ length: 6 }, (_, index) => `<i class="prize-card${index >= count ? ' taken' : ''}"></i>`).join('');
}

function benchSlots(cards) {
  return Array.from({ length: 5 }, (_, index) => window.TcgComponents.battlePokemon(cards[index], 'bench')).join('');
}

function hiddenHand(count) {
  const visible = Math.min(count, 9);
  return `${Array.from({ length: visible }, (_, index) => `<i class="card-back" style="--hand-index:${index};--hand-total:${visible}"></i>`).join('')}<span>${count}</span>`;
}

function renderPlayer(prefix, player, revealHand = false) {
  arenaEl[`${prefix}Name`].textContent = player.name;
  arenaEl[`${prefix}Counts`].textContent = `${player.deckCount} deck · ${player.handCount} hand · ${player.discardCount} discard`;
  arenaEl[`${prefix}Bench`].innerHTML = benchSlots(player.bench);
  arenaEl[`${prefix}Active`].innerHTML = window.TcgComponents.battlePokemon(player.active, 'active');
  arenaEl[`${prefix}Prizes`].innerHTML = prizeCards(player.prizesRemaining);
  const deckTarget = prefix === 'player' ? arenaEl.playerDeckStack : arenaEl.opponentDeck;
  deckTarget.innerHTML = `<span>${player.deckCount}</span><small>deck</small>`;
  if (revealHand) {
    arenaEl.playerHand.innerHTML = player.hand.map(window.TcgComponents.handCard).join('') || '<p class="empty-hand">Your hand is empty.</p>';
    arenaEl.handCount.textContent = `${player.handCount} card${player.handCount === 1 ? '' : 's'}`;
  } else {
    arenaEl.opponentHand.innerHTML = hiddenHand(player.handCount);
  }
}

function actionGroup(type) {
  return ({ call_coin: 'Coin flip', choose_turn_order: 'Turn order', mulligan_draw: 'Mulligan', choose_active: 'Active', setup_bench: 'Setup Bench', finish_setup: 'Setup', attach: 'Energy', bench: 'Bench', evolve: 'Evolution', play_trainer: 'Trainer', retreat: 'Retreat', promote: 'Promotion', attack: 'Attack', end_turn: 'Turn' })[type] || 'Other';
}

function actionMarkup(actions, includeGroups = true) {
  let previousGroup = '';
  return actions.map((action, index) => {
    const group = actionGroup(action.type);
    const heading = includeGroups && group !== previousGroup ? `<span class="action-group">${escapeHtml(group)}</span>` : '';
    previousGroup = group;
    return `${heading}<button type="button" data-action-index="${index}" class="action-${escapeHtml(action.type)}">${escapeHtml(action.label)}</button>`;
  }).join('') || '<p>No actions available.</p>';
}

function renderSetup(game) {
  const active = game.status === 'setup';
  arenaEl.setupOverlay.hidden = !active;
  if (!active) return;
  arenaEl.setupStep.textContent = `Match setup · ${String(game.phase).replaceAll('_', ' ')}`;
  arenaEl.setupTitle.textContent = game.prompt.title;
  arenaEl.setupText.textContent = game.prompt.text;
  arenaEl.setupActionList.innerHTML = actionMarkup(game.legalActions, false);
  arenaEl.setupCoin.className = `setup-coin${game.setup.coinResult ? ` result-${game.setup.coinResult}` : ''}`;
  const facts = [];
  if (game.setup.coinCall) facts.push(`You called ${game.setup.coinCall}`);
  if (game.setup.coinResult) facts.push(`Result: ${game.setup.coinResult}`);
  if (game.setup.firstPlayer) facts.push(`${game.setup.firstPlayer === 'player' ? 'You go' : 'Opponent goes'} first`);
  if (game.setup.playerMulligans || game.setup.opponentMulligans) facts.push(`Mulligans: you ${game.setup.playerMulligans}, opponent ${game.setup.opponentMulligans}`);
  arenaEl.setupFacts.innerHTML = facts.map(fact => `<span>${escapeHtml(fact)}</span>`).join('');
}

function renderGame(game) {
  arenaState.game = game;
  arenaState.selectedCardUid = null;
  arenaEl.arenaLobby.hidden = true;
  arenaEl.gameShell.hidden = false;
  arenaEl.arenaStatus.textContent = game.status === 'finished' ? 'Match finished' : (game.status === 'setup' ? 'Match setup' : `Turn ${game.turn}`);
  arenaEl.turnNumber.textContent = game.status === 'setup' ? 'Setup' : `Turn ${game.turn}`;
  arenaEl.turnStatus.textContent = game.status === 'finished'
    ? (game.winner === 'player' ? 'You won' : 'Opponent won')
    : (game.status === 'setup' ? 'Pregame' : (game.isPlayerTurn ? 'Your turn' : "Opponent's turn"));
  arenaEl.battlePhase.textContent = game.status === 'finished'
    ? 'Match complete'
    : (game.status === 'setup' ? game.prompt.title : (game.isPlayerTurn ? 'Main phase' : 'Opponent thinking'));
  arenaEl.battleMessage.textContent = game.status === 'finished'
    ? `${game.winner === 'player' ? 'Victory' : 'Defeat'} · ${String(game.reason || '').replace('_', ' ')}`
    : (game.status === 'setup' ? game.prompt.text : 'Choose one legal action. An attack ends your turn.');
  renderPlayer('opponent', game.opponent);
  renderPlayer('player', game.player, true);
  renderSetup(game);

  arenaEl.actionList.innerHTML = actionMarkup(game.legalActions);
  arenaEl.battleLog.innerHTML = [...game.log].reverse().map(item => `<li>${escapeHtml(item)}</li>`).join('');
  arenaEl.arenaLimitations.innerHTML = [
    ...(game.ruleCoverage || []).map(item => `${item.rule}: ${item.status} — ${item.detail}`),
    ...game.limitations,
  ].map(item => `<li>${escapeHtml(item)}</li>`).join('');
  localStorage.setItem('ptcglArenaSession', game.sessionId);
}

async function loadDecks() {
  try {
    const result = await arenaApi('/api/decks');
    const decks = result.items.filter(deck => deck.format === 'standard' && deck.card_count === 60);
    arenaEl.playerDeck.replaceChildren(new Option(decks.length ? 'Choose a deck' : 'No valid Standard decks', ''));
    for (const deck of decks) arenaEl.playerDeck.add(new Option(`${deck.name} (${deck.card_count})`, deck.id));
    arenaEl.startMatchButton.disabled = !decks.length;
    arenaEl.lobbyMessage.textContent = decks.length
      ? 'Only saved, valid 60-card Standard decks appear here.'
      : 'Create and save a valid Standard deck in Deck Lab first.';
  } catch (error) {
    arenaEl.lobbyMessage.textContent = error.message;
    arenaEl.startMatchButton.disabled = true;
  }
}

async function startMatch(event) {
  event.preventDefault();
  if (!arenaEl.playerDeck.value || arenaState.busy) return;
  arenaState.busy = true;
  arenaEl.startMatchButton.disabled = true;
  arenaEl.lobbyMessage.textContent = 'Shuffling decks and setting Prize cards…';
  try {
    renderGame(await arenaApi('/api/arena/sessions', {
      method: 'POST',
      body: JSON.stringify({
        deckId: arenaEl.playerDeck.value,
        opponentType: arenaEl.opponentType.value,
        seed: Number(arenaEl.arenaSeed.value || 1),
      }),
    }));
  } catch (error) {
    arenaEl.lobbyMessage.textContent = error.message;
  } finally {
    arenaState.busy = false;
    arenaEl.startMatchButton.disabled = !arenaEl.playerDeck.value;
  }
}

async function submitAction(action) {
  if (!action || arenaState.busy || !arenaState.game) return;
  arenaState.busy = true;
  document.querySelectorAll('[data-action-index]').forEach(item => { item.disabled = true; });
  arenaEl.battleMessage.textContent = action.type === 'attack' || action.type === 'end_turn' || action.type === 'finish_setup' ? 'Resolving the next game step…' : 'Applying choice…';
  try {
    renderGame(await arenaApi(`/api/arena/sessions/${arenaState.game.sessionId}/actions`, {
      method: 'POST', body: JSON.stringify(action),
    }));
  } catch (error) {
    arenaEl.battleMessage.textContent = error.message;
  } finally {
    arenaState.busy = false;
  }
}

function playAction(event) {
  const button = event.target.closest('[data-action-index]');
  if (!button || !arenaState.game) return;
  submitAction(arenaState.game.legalActions[Number(button.dataset.actionIndex)]);
}

function selectHandCard(event) {
  const card = event.target.closest('[data-card-uid]');
  if (!card || !arenaState.game || arenaState.busy) return;
  const cardUid = card.dataset.cardUid;
  const related = arenaState.game.legalActions
    .map((action, index) => ({ action, index }))
    .filter(item => item.action.cardUid === cardUid);
  arenaEl.playerHand.querySelectorAll('.hand-card').forEach(item => item.classList.toggle('selected', item === card));
  arenaEl.actionList.querySelectorAll('button').forEach(button => {
    button.classList.toggle('related', related.some(item => item.index === Number(button.dataset.actionIndex)));
    button.classList.toggle('unrelated', related.length > 0 && !related.some(item => item.index === Number(button.dataset.actionIndex)));
  });
  arenaState.selectedCardUid = related.length ? cardUid : null;
  arenaEl.battleMessage.textContent = related.length
    ? (related.some(item => item.action.targetUid) ? 'Now choose a highlighted move or click one of your Pokémon.' : 'Choose the highlighted move to play this card.')
    : 'That card has no legal play right now.';
}

function selectPokemon(event) {
  const pokemon = event.target.closest('[data-pokemon-uid]');
  if (!pokemon || !arenaState.game || arenaState.busy) return;
  const targetUid = pokemon.dataset.pokemonUid;
  const matching = arenaState.game.legalActions.filter(action =>
    action.targetUid === targetUid && (!arenaState.selectedCardUid || action.cardUid === arenaState.selectedCardUid)
  );
  if (matching.length === 1) submitAction(matching[0]);
}

async function toggleFullscreen() {
  try {
    if (document.fullscreenElement === arenaEl.gameTable) await document.exitFullscreen();
    else await arenaEl.gameTable.requestFullscreen();
  } catch (error) {
    arenaEl.battleMessage.textContent = `Fullscreen is unavailable: ${error.message}`;
  }
}

function syncFullscreenLabel() {
  const active = document.fullscreenElement === arenaEl.gameTable;
  arenaEl.fullscreenLabel.textContent = active ? 'Exit fullscreen' : 'Fullscreen';
  arenaEl.fullscreenButton.setAttribute('aria-label', active ? 'Exit fullscreen Arena' : 'Enter fullscreen Arena');
}

function newMatch() {
  if (document.fullscreenElement) document.exitFullscreen();
  localStorage.removeItem('ptcglArenaSession');
  arenaState.game = null;
  arenaEl.gameShell.hidden = true;
  arenaEl.arenaLobby.hidden = false;
  arenaEl.arenaStatus.textContent = 'Ready';
}

async function restoreMatch() {
  const sessionId = localStorage.getItem('ptcglArenaSession');
  if (!sessionId) return;
  try {
    renderGame(await arenaApi(`/api/arena/sessions/${sessionId}`));
  } catch {
    localStorage.removeItem('ptcglArenaSession');
  }
}

arenaEl.startMatchForm.addEventListener('submit', startMatch);
arenaEl.actionList.addEventListener('click', playAction);
arenaEl.setupActionList.addEventListener('click', playAction);
arenaEl.newMatchButton.addEventListener('click', newMatch);
arenaEl.fullscreenButton.addEventListener('click', toggleFullscreen);
arenaEl.playerHand.addEventListener('click', selectHandCard);
arenaEl.playerActive.addEventListener('click', selectPokemon);
arenaEl.playerBench.addEventListener('click', selectPokemon);
document.addEventListener('fullscreenchange', syncFullscreenLabel);
document.addEventListener('keydown', event => {
  if (event.key.toLowerCase() === 'f' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) toggleFullscreen();
});
arenaEl.playerDeck.addEventListener('change', () => { arenaEl.startMatchButton.disabled = !arenaEl.playerDeck.value; });
loadDecks();
restoreMatch();
