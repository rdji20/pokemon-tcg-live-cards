const el = Object.fromEntries([
  'logoutButton', 'standardCount', 'aiCount', 'humanCount', 'validatedCount', 'pendingCount',
  'loginPanel', 'loginForm', 'loginHelp', 'reviewerName', 'reviewPassword',
  'loginMessage', 'queueSection', 'queueSummary', 'reviewStatus', 'reviewQueue'
].map(id => [id, document.getElementById(id)]));

const number = new Intl.NumberFormat('en-US');

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
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function sourceText(item) {
  const card = item.raw_data || {};
  const sections = [];
  for (const ability of card.abilities || []) sections.push(`Ability — ${ability.name}\n${ability.text}`);
  for (const attack of card.attacks || []) sections.push(`Attack — ${attack.name} ${attack.damage || ''}\n${attack.text || 'No additional text.'}`);
  for (const rule of card.rules || []) sections.push(`Rule box\n${rule}`);
  return sections.join('\n\n') || 'This card has no printed effect text.';
}

async function loadCoverage() {
  const coverage = await api('/api/rule-coverage');
  el.standardCount.textContent = number.format(coverage.standard_cards);
  el.aiCount.textContent = number.format(coverage.ai_passed);
  el.humanCount.textContent = number.format(coverage.human_approved);
  el.validatedCount.textContent = number.format(coverage.fully_validated);
  el.pendingCount.textContent = number.format(coverage.pending_reviews);
}

function combinedStatus(item) {
  const ai = item.ai_status === 'passed';
  const human = item.manual_status === 'approved';
  if (ai && human) return 'Validated';
  if (ai) return 'AI checked';
  if (human) return 'Human checked';
  return 'Not checked';
}

function renderQueue(items) {
  el.queueSummary.textContent = `${items.length} ${el.reviewStatus.value} rule version${items.length === 1 ? '' : 's'}`;
  el.reviewQueue.innerHTML = items.length ? items.map(item => `
    <article class="review-card" data-review-id="${escapeHtml(item.id)}">
      <div class="review-card-art"><img src="${escapeHtml(item.image_small)}" alt="${escapeHtml(item.name)} card"></div>
      <div class="review-card-body">
        <div class="review-card-header">
          <div><h3>${escapeHtml(item.name)}</h3><p>${escapeHtml(item.set_name)} · ${escapeHtml(item.card_id)} · ${escapeHtml(item.ai_model)}</p></div>
          <span class="status-pill">${escapeHtml(combinedStatus(item))}</span>
        </div>
        <div class="review-checks"><span class="${item.ai_status === 'passed' ? 'passed' : 'failed'}">AI checks: ${escapeHtml(item.ai_status)}</span><span class="${item.manual_status === 'approved' ? 'passed' : item.manual_status === 'rejected' ? 'failed' : ''}">Human review: ${escapeHtml(item.manual_status)}</span></div>
        <div class="source-text">${escapeHtml(sourceText(item))}</div>
        ${item.automated_checks.errors?.length ? `<div class="check-errors"><strong>Automated check issues</strong><ul>${item.automated_checks.errors.map(error => `<li>${escapeHtml(error)}</li>`).join('')}</ul></div>` : ''}
        <details class="program-details"><summary>Executable JSON · ${item.automated_checks.ruleCount} rules · ${item.automated_checks.operationCount} operations</summary><pre>${escapeHtml(JSON.stringify(item.program, null, 2))}</pre></details>
        ${item.manual_status === 'pending' ? `<div class="review-actions"><input data-note placeholder="Optional review note"><button class="reject-button" data-decision="rejected" type="button">Reject</button><button class="primary-button" data-decision="approved" type="button">Human approve</button></div>` : `<div class="result-box">Reviewed by ${escapeHtml(item.reviewer || 'unknown')}${item.review_note ? ` · ${escapeHtml(item.review_note)}` : ''}</div>`}
      </div>
    </article>
  `).join('') : '<div class="empty-queue">No rules in this queue.</div>';
}

async function loadQueue() {
  try {
    const result = await api(`/api/reviews?status=${encodeURIComponent(el.reviewStatus.value)}&limit=100`);
    renderQueue(result.items);
  } catch (error) {
    if (error.message.includes('login')) showLogin();
    else el.reviewQueue.innerHTML = `<div class="empty-queue">${escapeHtml(error.message)}</div>`;
  }
}

function showLogin(configured = true) {
  el.loginPanel.hidden = false;
  el.queueSection.hidden = true;
  el.logoutButton.hidden = true;
  if (!configured) el.loginHelp.textContent = 'Set PTCGL_REVIEW_PASSWORD to a password of at least 12 characters, then restart the server.';
}

function showQueue() {
  el.loginPanel.hidden = true;
  el.queueSection.hidden = false;
  el.logoutButton.hidden = false;
  loadQueue();
}

async function initialize() {
  await loadCoverage();
  const status = await api('/api/review/status');
  if (status.authenticated) showQueue();
  else showLogin(status.configured);
}

el.loginForm.addEventListener('submit', async event => {
  event.preventDefault();
  el.loginMessage.textContent = '';
  try {
    await api('/api/review/login', {
      method: 'POST',
      body: JSON.stringify({ reviewer: el.reviewerName.value, password: el.reviewPassword.value }),
    });
    el.reviewPassword.value = '';
    showQueue();
  } catch (error) {
    el.loginMessage.textContent = error.message;
  }
});

el.logoutButton.addEventListener('click', async () => {
  await api('/api/review/logout', { method: 'POST', body: '{}' });
  showLogin();
});

el.reviewStatus.addEventListener('change', loadQueue);
el.reviewQueue.addEventListener('click', async event => {
  const button = event.target.closest('[data-decision]');
  if (!button) return;
  const card = button.closest('[data-review-id]');
  const note = card.querySelector('[data-note]').value;
  button.disabled = true;
  try {
    await api(`/api/reviews/${card.dataset.reviewId}/decision`, {
      method: 'POST', body: JSON.stringify({ decision: button.dataset.decision, note }),
    });
    await Promise.all([loadCoverage(), loadQueue()]);
  } catch (error) {
    button.disabled = false;
    window.alert(error.message);
  }
});

initialize().catch(error => {
  el.loginMessage.textContent = error.message;
});
