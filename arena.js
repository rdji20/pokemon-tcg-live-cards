const arenaState = { game: null, busy: false, selectedCardUid: null, selectedSource: null, clockReceivedAt: 0, timeoutSent: false };

const arenaEl = Object.fromEntries([
  'arenaStatus', 'arenaLobby', 'startMatchForm', 'playerDeck', 'opponentType',
  'arenaSeed', 'startMatchButton', 'lobbyMessage', 'gameShell', 'turnStatus',
  'turnNumber', 'newMatchButton', 'gameTable', 'fullscreenButton', 'fullscreenLabel',
  'endTurnButton', 'contextMenu', 'contextTitle', 'contextActions', 'contextCancel',
  'battlePhase', 'stadiumZone', 'setupOverlay', 'setupCoin', 'setupStep', 'setupTitle',
  'setupText', 'setupActionList', 'setupFacts', 'opponentName', 'opponentCounts', 'opponentHand',
  'opponentBench', 'opponentPrizes', 'opponentActive', 'opponentDeck', 'opponentClock',
  'playerName', 'playerCounts', 'playerBench', 'playerPrizes', 'playerActive',
  'playerDeckStack', 'playerHand', 'handCount', 'battleMessage', 'playerClock',
  'battleLog', 'ruleTrace', 'arenaLimitations'
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

function setupCardBack(zone) {
  return `<article class="battle-pokemon ${zone} setup-card-back" aria-label="Face-down setup Pokémon"><i></i></article>`;
}

function setupBenchSlots(count) {
  return Array.from({ length: 5 }, (_, index) => index < count ? setupCardBack('bench') : window.TcgComponents.battlePokemon(null, 'bench')).join('');
}

function hiddenHand(count) {
  const visible = Math.min(count, 9);
  return `${Array.from({ length: visible }, (_, index) => `<i class="card-back" style="--hand-index:${index};--hand-total:${visible}"></i>`).join('')}<span>${count}</span>`;
}

function formatClock(milliseconds) {
  const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  return `${minutes}:${String(totalSeconds % 60).padStart(2, '0')}`;
}

function renderClocks() {
  const game = arenaState.game;
  if (!game?.clocks) return;
  const elapsed = game.status === 'finished' ? 0 : performance.now() - arenaState.clockReceivedAt;
  const playerMs = Math.max(0, game.clocks.playerMs - (game.clocks.active === 'player' ? elapsed : 0));
  const opponentMs = Math.max(0, game.clocks.opponentMs - (game.clocks.active === 'opponent' ? elapsed : 0));
  for (const [side, element, remaining] of [
    ['player', arenaEl.playerClock, playerMs],
    ['opponent', arenaEl.opponentClock, opponentMs],
  ]) {
    element.textContent = formatClock(remaining);
    element.dateTime = `PT${Math.ceil(remaining / 1000)}S`;
    element.classList.toggle('active', game.clocks.active === side && game.status !== 'finished');
    element.classList.toggle('urgent', remaining <= 60_000);
  }
  if (game.clocks.active === 'player' && playerMs <= 0 && game.status !== 'finished' && !arenaState.timeoutSent) {
    arenaState.timeoutSent = true;
    submitAction({ type: 'timeout' });
  }
}

function renderPlayer(prefix, player, revealHand = false) {
  const concealBoard = arenaState.game?.status === 'setup';
  arenaEl[`${prefix}Name`].textContent = player.name;
  arenaEl[`${prefix}Counts`].textContent = `${player.deckCount} deck / ${player.handCount} hand / ${player.discardCount} discard`;
  arenaEl[`${prefix}Bench`].innerHTML = concealBoard ? setupBenchSlots(player.bench.length) : benchSlots(player.bench);
  arenaEl[`${prefix}Active`].innerHTML = concealBoard && player.active ? setupCardBack('active') : window.TcgComponents.battlePokemon(player.active, 'active');
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
  return ({ call_coin: 'Coin flip', choose_turn_order: 'Turn order', mulligan_draw: 'Mulligan', choose_active: 'Active', setup_bench: 'Setup Bench', finish_setup: 'Setup', attach: 'Energy', bench: 'Bench', evolve: 'Evolution', play_trainer: 'Trainer', play_stadium: 'Stadium', attach_tool: 'Pokémon Tool', activate_ability: 'Ability', retreat: 'Retreat', promote: 'Promotion', attack: 'Attack', end_turn: 'Turn' })[type] || 'Other';
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

function setupActionMarkup(game) {
  if (game.phase === 'coin_call') {
    return game.legalActions.map((action, index) => `
      <button type="button" data-action-index="${index}" class="coin-choice coin-${escapeHtml(action.choice)}">
        <span class="coin-face" aria-hidden="true"><b>${action.choice === 'heads' ? 'H' : 'T'}</b></span>
        <strong>${action.choice === 'heads' ? 'Heads' : 'Tails'}</strong>
      </button>
    `).join('');
  }
  if (game.phase === 'choose_turn_order') {
    return game.legalActions.map((action, index) => `
      <button type="button" data-action-index="${index}" class="turn-choice">
        <strong>${action.order === 'first' ? 'Go first' : 'Go second'}</strong>
        <span>${action.order === 'first' ? 'Build your board first' : 'Attack on your first turn'}</span>
      </button>
    `).join('');
  }
  return actionMarkup(game.legalActions, false);
}

function renderSetup(game) {
  const active = game.status === 'setup' && ['coin_call', 'choose_turn_order', 'mulligan_draw'].includes(game.phase);
  arenaEl.setupOverlay.hidden = !active;
  if (!active) return;
  arenaEl.setupStep.textContent = `Match setup / ${String(game.phase).replaceAll('_', ' ')}`;
  arenaEl.setupTitle.textContent = game.prompt.title;
  arenaEl.setupText.textContent = game.prompt.text;
  arenaEl.setupActionList.innerHTML = setupActionMarkup(game);
  arenaEl.setupCoin.hidden = game.phase !== 'choose_turn_order';
  arenaEl.setupCoin.className = `setup-coin${game.setup.coinResult ? ` result-${game.setup.coinResult}` : ''}`;
  const facts = [];
  if (game.setup.coinCall) facts.push(`You called ${game.setup.coinCall}`);
  if (game.setup.coinResult) facts.push(`Result: ${game.setup.coinResult}`);
  if (game.setup.firstPlayer) facts.push(`${game.setup.firstPlayer === 'player' ? 'You go' : 'Opponent goes'} first`);
  if (game.setup.playerMulligans || game.setup.opponentMulligans) facts.push(`Mulligans: you ${game.setup.playerMulligans}, opponent ${game.setup.opponentMulligans}`);
  arenaEl.setupFacts.textContent = facts.join(' / ');
}

function renderGame(game) {
  arenaState.game = game;
  arenaState.clockReceivedAt = performance.now();
  arenaState.timeoutSent = false;
  document.body.classList.add('arena-playing');
  arenaState.selectedCardUid = null;
  arenaState.selectedSource = null;
  arenaEl.arenaLobby.hidden = true;
  arenaEl.gameShell.hidden = false;
  arenaEl.arenaStatus.textContent = game.status === 'finished' ? 'Match finished' : (game.status === 'setup' ? 'Match setup' : `Turn ${game.turn}`);
  arenaEl.turnNumber.textContent = game.status === 'setup' ? 'Setup' : `Turn ${game.turn}`;
  arenaEl.turnStatus.textContent = game.status === 'finished'
    ? (game.winner === 'player' ? 'You won' : (game.winner === 'opponent' ? 'Opponent won' : 'Sudden Death'))
    : (game.status === 'setup' ? 'Pregame' : (game.isPlayerTurn ? 'Your turn' : "Opponent's turn"));
  arenaEl.battlePhase.textContent = game.status === 'finished'
    ? 'Match complete'
    : (game.status === 'setup' ? game.prompt.title : (game.isPlayerTurn ? 'Main phase' : 'Opponent thinking'));
  arenaEl.battleMessage.textContent = game.status === 'finished'
    ? `${game.winner === 'player' ? 'Victory' : (game.winner === 'opponent' ? 'Defeat' : 'Tie')} / ${String(game.reason || '').replaceAll('_', ' ')}`
    : (game.status === 'setup' ? game.prompt.text : 'Select or drag a card, or click your Active Pokémon to attack.');
  renderPlayer('opponent', game.opponent);
  renderPlayer('player', game.player, true);
  renderSetup(game);
  arenaEl.stadiumZone.innerHTML = game.stadium
    ? `<button type="button" data-stadium-uid="${escapeHtml(game.stadium.uid)}"><img src="${escapeHtml(game.stadium.image || '')}" alt="${escapeHtml(game.stadium.name)} Stadium"><span>${escapeHtml(game.stadium.name)}</span></button>`
    : '<span>Stadium</span>';

  arenaEl.contextMenu.hidden = true;
  const primaryIndex = game.legalActions.findIndex(action => action.type === 'end_turn' || action.type === 'finish_setup');
  arenaEl.endTurnButton.hidden = primaryIndex < 0;
  arenaEl.endTurnButton.dataset.actionIndex = primaryIndex;
  arenaEl.endTurnButton.firstChild.textContent = game.legalActions[primaryIndex]?.type === 'finish_setup' ? 'Ready ' : 'End turn ';
  if (game.status === 'setup' && ['choose_active', 'choose_bench'].includes(game.phase)) {
    const playable = new Set(game.legalActions.filter(action => action.cardUid).map(action => action.cardUid));
    arenaEl.playerHand.querySelectorAll('[data-card-uid]').forEach(card => card.classList.toggle('playable', playable.has(card.dataset.cardUid)));
  }
  if (game.phase === 'playing' && game.legalActions.some(action => action.type === 'promote')) {
    game.legalActions.filter(action => action.type === 'promote').forEach(action => markTarget(action.targetUid));
    arenaEl.battleMessage.textContent = 'Choose a Benched Pokémon to move into the Active Spot.';
  }
  arenaEl.battleLog.innerHTML = [...game.log].reverse().map(item => `<li>${escapeHtml(item)}</li>`).join('');
  arenaEl.ruleTrace.innerHTML = [...(game.ruleTrace || [])].reverse().map(item => `
    <article class="trace-entry" title="${escapeHtml(item.ruleKey || item.ruleId)}">
      <div><strong>${escapeHtml(item.cardName)}</strong><span>${escapeHtml(item.ruleId)} / ${escapeHtml(item.review.manual)} review</span></div>
      <p>${escapeHtml(item.summary)}</p>
      <button type="button" data-flag-trace="${item.traceId}">Flag incorrect</button>
    </article>
  `).join('') || '<p class="empty-trace">No compiled card rule has executed yet.</p>';
  arenaEl.arenaLimitations.innerHTML = [
    ...(game.ruleCoverage || []).map(item => `${item.rule}: ${item.status} — ${item.detail}`),
    ...game.limitations,
  ].map(item => `<li>${escapeHtml(item)}</li>`).join('');
  localStorage.setItem('ptcglArenaSession', game.sessionId);
  renderClocks();
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
  arenaEl.lobbyMessage.textContent = 'Preparing the decks and opening coin flip…';
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
    document.querySelectorAll('[data-action-index]').forEach(item => { item.disabled = false; });
  } finally {
    arenaState.busy = false;
  }
}

async function flagRule(event) {
  const button = event.target.closest('[data-flag-trace]');
  if (!button || !arenaState.game || button.disabled) return;
  button.disabled = true;
  try {
    await arenaApi(`/api/arena/sessions/${arenaState.game.sessionId}/rule-reports`, {
      method: 'POST',
      body: JSON.stringify({ traceId: Number(button.dataset.flagTrace), reason: 'Incorrect card behavior' }),
    });
    button.textContent = 'Flagged for correction';
    arenaEl.battleMessage.textContent = 'That exact rule version and game event were saved for correction.';
  } catch (error) {
    button.disabled = false;
    arenaEl.battleMessage.textContent = error.message;
  }
}

function playAction(event) {
  const button = event.target.closest('[data-action-index]');
  if (!button || !arenaState.game) return;
  submitAction(arenaState.game.legalActions[Number(button.dataset.actionIndex)]);
}

function indexedActions(predicate) {
  return arenaState.game.legalActions
    .map((action, index) => ({ action, index }))
    .filter(item => predicate(item.action));
}

function clearInteraction() {
  arenaState.selectedCardUid = null;
  arenaState.selectedSource = null;
  arenaEl.contextMenu.hidden = true;
  arenaEl.contextActions.replaceChildren();
  arenaEl.playerHand.querySelectorAll('.hand-card').forEach(item => item.classList.remove('selected'));
  arenaEl.gameTable.querySelectorAll('.target-ready').forEach(item => item.classList.remove('target-ready'));
  arenaEl.playerBench.classList.remove('target-ready-zone');
}

function markTarget(uid) {
  arenaEl.gameTable.querySelectorAll('[data-pokemon-uid]').forEach(item => {
    if (item.dataset.pokemonUid === uid) item.classList.add('target-ready');
  });
}

function showContext(title, items) {
  arenaEl.contextTitle.textContent = title;
  arenaEl.contextActions.innerHTML = items.map(({ action, index }) =>
    `<button type="button" data-action-index="${index}" class="context-${escapeHtml(action.type)}">${escapeHtml(action.label.replace(/^Attack:\s*/, ''))}</button>`
  ).join('');
  arenaEl.contextMenu.hidden = items.length === 0;
}

function selectHandCard(event) {
  const card = event.target.closest('[data-card-uid]');
  if (!card || !arenaState.game || arenaState.busy) return;
  const cardUid = card.dataset.cardUid;
  const related = indexedActions(action => action.cardUid === cardUid);
  if (arenaState.game.status === 'setup') {
    const setupAction = related.find(item => ['choose_active', 'setup_bench'].includes(item.action.type));
    if (setupAction) submitAction(setupAction.action);
    else arenaEl.battleMessage.textContent = 'Choose a highlighted Basic Pokémon.';
    return;
  }
  if (arenaState.game.status !== 'playing') return;
  clearInteraction();
  if (!related.length) {
    arenaEl.battleMessage.textContent = 'That card cannot be played right now.';
    return;
  }
  arenaState.selectedCardUid = cardUid;
  card.classList.add('selected');
  related.filter(item => item.action.targetUid).forEach(item => markTarget(item.action.targetUid));
  const benchPlay = related.some(item => item.action.type === 'bench');
  if (benchPlay) arenaEl.playerBench.classList.add('target-ready-zone');
  const immediate = related.filter(item => !item.action.targetUid && item.action.type !== 'bench');
  showContext(card.querySelector('strong')?.textContent || 'Selected card', immediate);
  arenaEl.battleMessage.textContent = related.some(item => item.action.targetUid)
    ? 'Choose a glowing Pokémon as the target.'
    : (benchPlay ? 'Click or drop the card onto an open Bench spot.' : 'Confirm the selected card play.');
}

function selectPokemon(event) {
  const pokemon = event.target.closest('[data-pokemon-uid]');
  if (!pokemon || !arenaState.game || arenaState.busy) return;
  const targetUid = pokemon.dataset.pokemonUid;
  const matching = arenaState.game.legalActions.filter(action => {
    if (action.targetUid !== targetUid) return false;
    if (arenaState.selectedCardUid) return action.cardUid === arenaState.selectedCardUid;
    if (action.cardUid) return false;
    if (action.type === 'promote') return true;
    const selectedActive = arenaState.selectedSource === arenaState.game.player.active?.uid;
    return action.sourceUid === arenaState.selectedSource || (selectedActive && ['attack', 'retreat'].includes(action.type));
  });
  if (matching.length === 1) {
    submitAction(matching[0]);
    return;
  }
  if (arenaState.selectedCardUid) return;
  const activeCard = event.currentTarget === arenaEl.playerActive || pokemon.closest('#playerActive');
  const abilities = indexedActions(action => action.type === 'activate_ability' && action.sourceUid === targetUid);
  if (!activeCard && !abilities.length) return;
  clearInteraction();
  arenaState.selectedSource = targetUid;
  const attacks = activeCard ? indexedActions(action => action.type === 'attack') : [];
  const retreats = activeCard ? indexedActions(action => action.type === 'retreat') : [];
  abilities.filter(item => item.action.targetUid).forEach(item => markTarget(item.action.targetUid));
  retreats.forEach(item => markTarget(item.action.targetUid));
  showContext(pokemon.querySelector('strong')?.textContent || 'Pokémon', [...attacks, ...abilities.filter(item => !item.action.targetUid)]);
  arenaEl.battleMessage.textContent = attacks.length && retreats.length
    ? 'Choose an attack, or click a glowing Benched Pokémon to retreat.'
    : (attacks.length || abilities.length ? 'Choose an attack or Ability.' : 'Click a glowing Benched Pokémon to retreat.');
}

function selectStadium(event) {
  const button = event.target.closest('[data-stadium-uid]');
  if (!button || !arenaState.game || arenaState.busy) return;
  clearInteraction();
  arenaState.selectedSource = button.dataset.stadiumUid;
  const abilities = indexedActions(action => action.type === 'activate_ability' && action.sourceUid === button.dataset.stadiumUid);
  showContext(arenaState.game.stadium?.name || 'Stadium', abilities);
  arenaEl.battleMessage.textContent = abilities.length ? 'Choose the Stadium effect.' : 'This Stadium has no available action right now.';
}

function selectBenchZone(event) {
  if (event.target.closest('[data-pokemon-uid]')) {
    selectPokemon(event);
    return;
  }
  if (!arenaState.selectedCardUid || arenaState.busy) return;
  const action = arenaState.game.legalActions.find(item => item.type === 'bench' && item.cardUid === arenaState.selectedCardUid);
  if (action) submitAction(action);
}

function beginCardDrag(event) {
  const card = event.target.closest('[data-card-uid]');
  if (!card || arenaState.game?.status !== 'playing') {
    event.preventDefault();
    return;
  }
  selectHandCard({ target: card });
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', card.dataset.cardUid);
}

function allowCardDrop(event) {
  if (!arenaState.selectedCardUid) return;
  const target = event.target.closest('[data-pokemon-uid]');
  const canTarget = target && arenaState.game.legalActions.some(action => action.cardUid === arenaState.selectedCardUid && action.targetUid === target.dataset.pokemonUid);
  const canBench = event.currentTarget === arenaEl.playerBench && arenaState.game.legalActions.some(action => action.type === 'bench' && action.cardUid === arenaState.selectedCardUid);
  if (canTarget || canBench) event.preventDefault();
}

function dropCard(event) {
  event.preventDefault();
  const cardUid = event.dataTransfer.getData('text/plain') || arenaState.selectedCardUid;
  const target = event.target.closest('[data-pokemon-uid]');
  const action = arenaState.game.legalActions.find(item =>
    item.cardUid === cardUid && (target ? item.targetUid === target.dataset.pokemonUid : item.type === 'bench')
  );
  if (action) submitAction(action);
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
  document.body.classList.remove('arena-playing');
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
arenaEl.setupActionList.addEventListener('click', playAction);
arenaEl.contextActions.addEventListener('click', playAction);
arenaEl.contextCancel.addEventListener('click', clearInteraction);
arenaEl.endTurnButton.addEventListener('click', playAction);
arenaEl.newMatchButton.addEventListener('click', newMatch);
arenaEl.fullscreenButton.addEventListener('click', toggleFullscreen);
arenaEl.playerHand.addEventListener('click', selectHandCard);
arenaEl.playerHand.addEventListener('dragstart', beginCardDrag);
arenaEl.playerHand.addEventListener('keydown', event => {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    selectHandCard(event);
  }
});
arenaEl.playerActive.addEventListener('click', selectPokemon);
arenaEl.playerBench.addEventListener('click', selectBenchZone);
arenaEl.stadiumZone.addEventListener('click', selectStadium);
arenaEl.ruleTrace.addEventListener('click', flagRule);
arenaEl.opponentBench.addEventListener('click', selectPokemon);
[arenaEl.playerActive, arenaEl.playerBench, arenaEl.opponentBench].forEach(target => {
  target.addEventListener('dragover', allowCardDrop);
  target.addEventListener('drop', dropCard);
});
document.addEventListener('fullscreenchange', syncFullscreenLabel);
document.addEventListener('keydown', event => {
  if (event.key.toLowerCase() === 'f' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) toggleFullscreen();
});
arenaEl.playerDeck.addEventListener('change', () => { arenaEl.startMatchButton.disabled = !arenaEl.playerDeck.value; });
loadDecks();
restoreMatch();
setInterval(renderClocks, 250);
