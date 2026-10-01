const arenaState = { game: null, busy: false };

const arenaEl = Object.fromEntries([
  'arenaStatus', 'arenaLobby', 'startMatchForm', 'playerDeck', 'opponentType',
  'arenaSeed', 'startMatchButton', 'lobbyMessage', 'gameShell', 'turnStatus',
  'turnNumber', 'newMatchButton', 'opponentName', 'opponentCounts',
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
  }
}

function actionGroup(type) {
  return ({ attach: 'Energy', bench: 'Bench', evolve: 'Evolution', play_trainer: 'Trainer', retreat: 'Retreat', promote: 'Promotion', attack: 'Attack', end_turn: 'Turn' })[type] || 'Other';
}

function renderGame(game) {
  arenaState.game = game;
  arenaEl.arenaLobby.hidden = true;
  arenaEl.gameShell.hidden = false;
  arenaEl.arenaStatus.textContent = game.status === 'finished' ? 'Match finished' : `Turn ${game.turn}`;
  arenaEl.turnNumber.textContent = `Turn ${game.turn}`;
  arenaEl.turnStatus.textContent = game.status === 'finished'
    ? (game.winner === 'player' ? 'You won' : 'Opponent won')
    : (game.isPlayerTurn ? 'Your turn' : "Opponent's turn");
  arenaEl.battleMessage.textContent = game.status === 'finished'
    ? `${game.winner === 'player' ? 'Victory' : 'Defeat'} · ${String(game.reason || '').replace('_', ' ')}`
    : 'Choose one legal action. An attack ends your turn.';
  renderPlayer('opponent', game.opponent);
  renderPlayer('player', game.player, true);

  let previousGroup = '';
  arenaEl.actionList.innerHTML = game.legalActions.map((action, index) => {
    const group = actionGroup(action.type);
    const heading = group !== previousGroup ? `<span class="action-group">${escapeHtml(group)}</span>` : '';
    previousGroup = group;
    return `${heading}<button type="button" data-action-index="${index}" class="action-${escapeHtml(action.type)}">${escapeHtml(action.label)}</button>`;
  }).join('') || '<p>No actions available.</p>';
  arenaEl.battleLog.innerHTML = [...game.log].reverse().map(item => `<li>${escapeHtml(item)}</li>`).join('');
  arenaEl.arenaLimitations.innerHTML = game.limitations.map(item => `<li>${escapeHtml(item)}</li>`).join('');
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

async function playAction(event) {
  const button = event.target.closest('[data-action-index]');
  if (!button || arenaState.busy || !arenaState.game) return;
  const action = arenaState.game.legalActions[Number(button.dataset.actionIndex)];
  arenaState.busy = true;
  arenaEl.actionList.querySelectorAll('button').forEach(item => { item.disabled = true; });
  arenaEl.battleMessage.textContent = action.type === 'attack' || action.type === 'end_turn' ? 'Opponent is choosing its turn…' : 'Applying move…';
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

function newMatch() {
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
arenaEl.newMatchButton.addEventListener('click', newMatch);
arenaEl.playerDeck.addEventListener('change', () => { arenaEl.startMatchButton.disabled = !arenaEl.playerDeck.value; });
loadDecks();
restoreMatch();
