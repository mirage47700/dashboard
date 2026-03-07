// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
const $ = id => document.getElementById(id);
const escHtml = s => String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const fmtDate = s => s ? new Date(s).toLocaleDateString('fr-FR',{day:'2-digit',month:'short',year:'numeric'}) : '—';
const fmtTime = s => s ? new Date(s).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'}) : '';
const fmtRelative = s => {
  if (!s) return '—';
  const diff = Date.now() - new Date(s).getTime();
  const m = Math.floor(diff/60000);
  if (m < 1) return 'A l\'instant';
  if (m < 60) return `il y a ${m}min`;
  const h = Math.floor(m/60);
  if (h < 24) return `il y a ${h}h`;
  return fmtDate(s);
};

async function api(url, method='GET', body=null) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------
function updateClock() {
  const n = new Date();
  const el = $('mcClock');
  if (el) el.textContent = n.toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
}
setInterval(updateClock, 1000);
updateClock();

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------
const TABS = ['board','calendar','ibkr','projects','memories','docs','team','office'];
let currentTab = 'board';

function switchTab(tab) {
  TABS.forEach(t => {
    $(`section-${t}`).classList.toggle('hidden', t !== tab);
    $(`tab-${t}`).classList.toggle('active', t === tab);
  });
  currentTab = tab;
  if (tab === 'calendar')  loadCalendar();
  if (tab === 'ibkr')      loadIbkr();
  if (tab === 'projects')  loadProjects();
  if (tab === 'memories')  loadMemories();
  if (tab === 'docs')      loadDocs();
  if (tab === 'team')      loadTeam();
  if (tab === 'office')    renderOffice();
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------
let activityItems = [];

function initSSE() {
  const es = new EventSource('/mission-control/events');
  es.onmessage = e => {
    try { handleEvent(JSON.parse(e.data)); } catch (_) {}
  };
  es.onerror = () => {};
}

function handleEvent(data) {
  if (data.type === 'heartbeat') {
    updateHeartbeatUI(data);
    if (currentTab === 'office') renderOffice();
  }
  if (data.type === 'task_created' || data.type === 'task_updated' || data.type === 'task_deleted') {
    if (currentTab === 'board') loadBoard();
  }
  if (data.type === 'activity') {
    prependActivity({agent: data.agent, action: data.action, timestamp: data.timestamp});
    prependTicker({agent: data.agent, action: data.action, timestamp: data.timestamp});
  }
}

function updateHeartbeatUI(data) {
  const hb = $('mcHeartbeat');
  const label = $('mcHbLabel');
  if (!hb) return;
  hb.classList.add('alive');
  if (label) label.textContent = `${data.agent} — ${fmtRelative(data.timestamp)}`;
}

// ---------------------------------------------------------------------------
// Heartbeat poll
// ---------------------------------------------------------------------------
async function pollHeartbeat() {
  try {
    const rows = await api('/mission-control/api/heartbeat');
    if (rows.length) {
      const latest = rows.sort((a,b) => b.last_beat.localeCompare(a.last_beat))[0];
      updateHeartbeatUI({ agent: latest.agent, timestamp: latest.last_beat });
    }
  } catch(_) {}
}

// ---------------------------------------------------------------------------
// BOARD
// ---------------------------------------------------------------------------
let allTasks = [];
let draggedId = null;

async function loadBoard() {
  const agent = $('boardAgentFilter')?.value || '';
  const params = agent ? `?assigned_to=${encodeURIComponent(agent)}` : '';
  try { allTasks = await api(`/mission-control/api/tasks${params}`); } catch(_) { allTasks = []; }
  renderBoard();
  await loadActivity();
}

function renderBoard() {
  const cols = { backlog:[], todo:[], in_progress:[], done:[] };
  for (const t of allTasks) {
    if (cols[t.status]) cols[t.status].push(t);
  }
  for (const [status, tasks] of Object.entries(cols)) {
    const container = $(`cards-${status}`);
    const count = $(`cnt-${status}`);
    if (!container) continue;
    if (count) count.textContent = tasks.length;
    container.innerHTML = tasks.map(t => `
      <div class="kanban-card priority-${t.priority}"
           draggable="true"
           ondragstart="dragStart(event,${t.id})"
           ondragend="dragEnd(event)"
           onclick="openTaskModal(${t.id})">
        <div class="card-title">${escHtml(t.title)}</div>
        <div class="card-meta">
          <span class="card-tag assigned">${escHtml(t.assigned_to)}</span>
          ${t.project_name ? `<span class="card-tag project">${escHtml(t.project_name)}</span>` : ''}
          <span class="card-tag">${t.priority}</span>
        </div>
      </div>
    `).join('');
  }
}

function dragStart(event, id) {
  draggedId = id;
  event.target.classList.add('dragging');
  event.dataTransfer.effectAllowed = 'move';
}
function dragEnd(event) { event.target.classList.remove('dragging'); }

async function dropTask(event, newStatus) {
  event.preventDefault();
  if (!draggedId) return;
  try {
    await api(`/mission-control/api/tasks/${draggedId}/status`, 'PATCH', { status: newStatus });
    await loadBoard();
  } catch(_) {}
  draggedId = null;
}

// ---------------------------------------------------------------------------
// ACTIVITY
// ---------------------------------------------------------------------------
async function loadActivity() {
  try { activityItems = await api('/mission-control/api/activity?limit=30'); } catch(_) { activityItems = []; }
  renderActivity();
}

function renderActivity() {
  const el = $('activityList');
  if (!el) return;
  if (!activityItems.length) {
    el.innerHTML = '<div style="padding:16px;text-align:center;color:#64748b;font-size:12px;">Aucune activite</div>';
    return;
  }
  el.innerHTML = activityItems.map(a => `
    <div class="activity-item">
      <div class="activity-agent">${escHtml(a.agent)}</div>
      <div class="activity-action">${escHtml(a.action)}</div>
      <div class="activity-time">${fmtRelative(a.timestamp)}</div>
    </div>
  `).join('');
}

function prependActivity(item) {
  activityItems.unshift(item);
  if (activityItems.length > 50) activityItems.pop();
  renderActivity();
}

// ---------------------------------------------------------------------------
// HEADER METRICS
// ---------------------------------------------------------------------------
async function loadHeaderMetrics() {
  try {
    const [usage, perf] = await Promise.all([
      api('/api/usage').catch(() => null),
      api('/api/ibkr/perf').catch(() => null),
    ]);

    const orEl = $('metricOR');
    if (orEl && usage?.openrouter?.connected) {
      const rem = usage.openrouter.remaining_credits ?? 0;
      orEl.textContent = `OR $${rem.toFixed(2)}`;
      orEl.className = 'mc-metric';
    }

    const pnlEl = $('metricPnl');
    if (pnlEl && perf?.ytd) {
      const pnl = perf.ytd.pnl_net ?? 0;
      pnlEl.textContent = `YTD ${pnl >= 0 ? '+' : ''}$${pnl.toFixed(0)}`;
      pnlEl.className = `mc-metric ${pnl >= 0 ? 'pos' : 'neg'}`;
    }
  } catch(_) {}
}

// ---------------------------------------------------------------------------
// CALENDAR (Google Calendar)
// ---------------------------------------------------------------------------
let calYear = new Date().getFullYear();
let calMonth = new Date().getMonth();
let calSelectedDate = new Date().toISOString().split('T')[0];
let calEvents = [];

async function loadCalendar() {
  const start = new Date(calYear, calMonth, 1).toISOString();
  const end   = new Date(calYear, calMonth + 1, 0, 23, 59).toISOString();
  try {
    const [local, google] = await Promise.all([
      api(`/api/events`).catch(() => []),
      api(`/api/events/google?start=${encodeURIComponent(start)}&end=${encodeURIComponent(end)}`).catch(() => []),
    ]);
    // Check google auth status
    const status = await api('/api/google/status').catch(() => ({ connected: false }));
    const btn = $('gcalConnectBtn');
    if (btn) btn.style.display = status.connected ? 'none' : '';

    calEvents = [...local, ...google].sort((a, b) => new Date(a.start_datetime) - new Date(b.start_datetime));
  } catch(_) { calEvents = []; }
  renderCalGrid();
  renderCalDay();
}

function renderCalGrid() {
  const body  = $('calGridBody');
  const label = $('calMonthLabel');
  if (!body) return;

  const firstDay = new Date(calYear, calMonth, 1);
  const lastDay  = new Date(calYear, calMonth + 1, 0);
  const startDow = (firstDay.getDay() + 6) % 7;
  const todayStr = new Date().toISOString().split('T')[0];

  if (label) label.textContent = firstDay.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });

  const eventDays = new Set(calEvents.map(e => e.start_datetime.split('T')[0]));
  const googleDays = new Set(calEvents.filter(e => e.source === 'google').map(e => e.start_datetime.split('T')[0]));

  let html = '';
  for (let i = 0; i < startDow; i++) html += '<div class="cal-cell other"></div>';
  for (let d = 1; d <= lastDay.getDate(); d++) {
    const dateStr = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const cls = ['cal-cell',
      dateStr === todayStr        ? 'today'      : '',
      dateStr === calSelectedDate ? 'selected'   : '',
      googleDays.has(dateStr)     ? 'has-google' : (eventDays.has(dateStr) ? 'has-event' : ''),
    ].filter(Boolean).join(' ');
    html += `<div class="${cls}" onclick="calSelectDay('${dateStr}')">${d}</div>`;
  }
  const rem = (startDow + lastDay.getDate()) % 7;
  if (rem) for (let i = 0; i < 7 - rem; i++) html += '<div class="cal-cell other"></div>';
  body.innerHTML = html;
}

function renderCalDay() {
  const listEl  = $('calEventsList');
  const titleEl = $('calDayTitle');
  if (!listEl) return;

  const dayEvents = calEvents.filter(e => e.start_datetime.startsWith(calSelectedDate));
  if (titleEl) titleEl.textContent = new Date(calSelectedDate + 'T00:00:00').toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });

  if (!dayEvents.length) {
    listEl.innerHTML = '<div class="cal-empty">Aucun événement</div>';
    return;
  }
  listEl.innerHTML = dayEvents.map(e => `
    <div class="cal-event-item ${e.source === 'google' ? 'google' : ''}">
      <div class="cal-event-time">${fmtTime(e.start_datetime)}</div>
      <div>
        <div class="cal-event-title">${escHtml(e.title)}</div>
        ${e.description ? `<div class="cal-event-desc">${escHtml(e.description)}</div>` : ''}
      </div>
    </div>
  `).join('');
}

function calSelectDay(dateStr) {
  calSelectedDate = dateStr;
  renderCalGrid();
  renderCalDay();
}

function calGoPrev() {
  calMonth--;
  if (calMonth < 0) { calMonth = 11; calYear--; }
  loadCalendar();
}

function calGoNext() {
  calMonth++;
  if (calMonth > 11) { calMonth = 0; calYear++; }
  loadCalendar();
}

function calGoToday() {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  calSelectedDate = now.toISOString().split('T')[0];
  loadCalendar();
}

// ---------------------------------------------------------------------------
// IBKR
// ---------------------------------------------------------------------------
let ibkrPerf   = null;
let ibkrTrades = [];

async function loadIbkr() {
  try {
    [ibkrPerf, ibkrTrades] = await Promise.all([
      api('/api/ibkr/perf').catch(() => null),
      api('/api/ibkr/trades?limit=500').catch(() => []),
    ]);
  } catch(_) {}
  renderIbkrStats();
  renderIbkrMonthly();
  populateIbkrMonthFilter();
  renderIbkrTrades();
}

function renderIbkrStats() {
  const el = $('ibkrStats');
  if (!el) return;
  if (!ibkrPerf?.ytd) {
    el.innerHTML = '<div class="ibkr-empty">Aucune donnée IBKR. Configurez IBKR_FLEX_TOKEN et IBKR_FLEX_QUERY_ID.</div>';
    return;
  }
  const y = ibkrPerf.ytd;
  el.innerHTML = `
    <div class="ibkr-stat"><div class="ibkr-stat-label">PnL brut YTD</div><div class="ibkr-stat-val ${y.pnl >= 0 ? 'pos' : 'neg'}">${y.pnl >= 0 ? '+' : ''}$${y.pnl.toFixed(2)}</div></div>
    <div class="ibkr-stat"><div class="ibkr-stat-label">PnL net YTD</div><div class="ibkr-stat-val ${y.pnl_net >= 0 ? 'pos' : 'neg'}">${y.pnl_net >= 0 ? '+' : ''}$${y.pnl_net.toFixed(2)}</div></div>
    <div class="ibkr-stat"><div class="ibkr-stat-label">Commissions</div><div class="ibkr-stat-val neg">-$${Math.abs(y.commission).toFixed(2)}</div></div>
    <div class="ibkr-stat"><div class="ibkr-stat-label">Trades</div><div class="ibkr-stat-val">${y.trades}</div></div>
    <div class="ibkr-stat"><div class="ibkr-stat-label">Win rate</div><div class="ibkr-stat-val ${y.win_rate >= 50 ? 'pos' : 'neg'}">${y.win_rate.toFixed(1)}%</div></div>
    <div class="ibkr-stat"><div class="ibkr-stat-label">Dernière sync</div><div class="ibkr-stat-val" style="font-size:11px">${ibkrPerf.last_sync ? fmtRelative(ibkrPerf.last_sync) : '—'}</div></div>
  `;
}

function renderIbkrMonthly() {
  const el = $('ibkrMonthly');
  if (!el) return;
  if (!ibkrPerf?.monthly?.length) { el.innerHTML = ''; return; }
  el.innerHTML = `
    <table class="ibkr-table">
      <thead><tr><th>Mois</th><th>PnL brut</th><th>PnL net</th><th>Commission</th><th>Trades</th><th>Win %</th></tr></thead>
      <tbody>${ibkrPerf.monthly.map(m => `
        <tr>
          <td>${m.month}</td>
          <td class="${m.pnl >= 0 ? 'pos' : 'neg'}">${m.pnl >= 0 ? '+' : ''}$${m.pnl.toFixed(2)}</td>
          <td class="${m.pnl_net >= 0 ? 'pos' : 'neg'}">${m.pnl_net >= 0 ? '+' : ''}$${m.pnl_net.toFixed(2)}</td>
          <td class="neg">-$${Math.abs(m.commission).toFixed(2)}</td>
          <td>${m.trades}</td>
          <td class="${m.win_rate >= 50 ? 'pos' : 'neg'}">${m.win_rate.toFixed(1)}%</td>
        </tr>
      `).join('')}</tbody>
    </table>
  `;
}

function populateIbkrMonthFilter() {
  const sel = $('ibkrMonthFilter');
  if (!sel) return;
  const months = [...new Set(ibkrTrades.map(t => (t.trade_date || '').substring(0, 7)).filter(Boolean))].sort().reverse();
  sel.innerHTML = '<option value="">Tous les mois</option>' + months.map(m => `<option value="${m}">${m}</option>`).join('');
}

function renderIbkrTrades() {
  const el = $('ibkrTradesTable');
  if (!el) return;
  const filter = $('ibkrMonthFilter')?.value || '';
  const trades = filter ? ibkrTrades.filter(t => (t.trade_date || '').startsWith(filter)) : ibkrTrades;
  if (!trades.length) { el.innerHTML = '<div class="ibkr-empty">Aucun trade</div>'; return; }
  el.innerHTML = `
    <table class="ibkr-table">
      <thead><tr><th>Date</th><th>Symbole</th><th>B/S</th><th>Qté</th><th>Prix</th><th>Produit</th><th>PnL</th><th>Commission</th></tr></thead>
      <tbody>${trades.slice(0, 200).map(t => `
        <tr>
          <td class="muted">${t.trade_date || '—'}</td>
          <td style="font-weight:500">${escHtml(t.symbol)}</td>
          <td class="${t.buy_sell === 'BUY' ? 'pos' : 'neg'}">${t.buy_sell}</td>
          <td>${t.quantity}</td>
          <td>$${parseFloat(t.price || 0).toFixed(2)}</td>
          <td>$${parseFloat(t.proceeds || 0).toFixed(2)}</td>
          <td class="${(t.pnl || 0) >= 0 ? 'pos' : 'neg'}">${(t.pnl || 0) !== 0 ? ((t.pnl >= 0 ? '+' : '') + '$' + (t.pnl || 0).toFixed(2)) : '—'}</td>
          <td class="neg">-$${Math.abs(t.commission || 0).toFixed(2)}</td>
        </tr>
      `).join('')}</tbody>
    </table>
  `;
}

async function syncIbkr(btn) {
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    await api('/api/ibkr/sync', 'POST');
    await Promise.all([loadIbkr(), loadHeaderMetrics()]);
  } catch(e) { alert('Erreur sync: ' + e.message); }
  if (btn) { btn.disabled = false; btn.textContent = '↻ Sync'; }
}

// ---------------------------------------------------------------------------
// PROJECTS
// ---------------------------------------------------------------------------
let allProjects = [];

async function loadProjects() {
  try { allProjects = await api('/mission-control/api/projects'); } catch(_) { allProjects = []; }
  renderProjects();
}

function renderProjects() {
  const grid = $('projectsGrid');
  if (!grid) return;
  if (!allProjects.length) {
    grid.innerHTML = '<div style="color:#64748b;padding:40px;text-align:center">Aucun projet. Creez-en un.</div>';
    return;
  }
  grid.innerHTML = allProjects.map(p => {
    const pct = Math.min(100, Math.max(0, p.progress || 0));
    const auto = p.task_count > 0 ? Math.round((p.done_count / p.task_count) * 100) : p.progress;
    const display = p.task_count > 0 ? auto : pct;
    return `
      <div class="project-card" onclick="openProjectModal(${p.id})">
        <div class="project-status ${p.status}">${p.status}</div>
        <div class="project-name">${escHtml(p.name)}</div>
        <div class="project-desc">${escHtml(p.description || 'Aucune description')}</div>
        <div class="project-progress-bar"><div class="project-progress-fill" style="width:${display}%"></div></div>
        <div class="project-meta">
          <span>${display}% complete</span>
          <span>${p.done_count || 0}/${p.task_count || 0} taches</span>
          <span>${fmtDate(p.created_at)}</span>
        </div>
      </div>
    `;
  }).join('');
}

// ---------------------------------------------------------------------------
// MEMORIES
// ---------------------------------------------------------------------------
async function loadMemories() {
  const el = $('memoriesContainer');
  if (!el) return;
  el.innerHTML = '<div style="color:#64748b;padding:20px">Chargement...</div>';
  try {
    const days = await api('/mission-control/api/memories');
    if (!days.length) {
      el.innerHTML = '<div style="color:#64748b;padding:40px;text-align:center">Aucune memoire trouvee dans memories.md</div>';
      return;
    }
    el.innerHTML = days.map(d => `
      <div class="memory-day">
        <div class="memory-date">${escHtml(d.date)}</div>
        <div class="memory-items">
          ${d.items.map(item => `<div class="memory-item">${escHtml(item)}</div>`).join('')}
        </div>
      </div>
    `).join('');
  } catch(_) {
    el.innerHTML = '<div style="color:#ef4444;padding:20px">Erreur de lecture memories.md</div>';
  }
}

// ---------------------------------------------------------------------------
// DOCS
// ---------------------------------------------------------------------------
let allDocs = [];

async function loadDocs() {
  try { allDocs = await api('/mission-control/api/docs'); } catch(_) { allDocs = []; }
  renderDocs(allDocs);
}

function filterDocs() {
  const q     = $('docsSearch')?.value.toLowerCase() || '';
  const cat   = $('docsCatFilter')?.value || '';
  const filt  = allDocs.filter(d =>
    (!q   || d.title.toLowerCase().includes(q) || (d.content||'').toLowerCase().includes(q)) &&
    (!cat || d.category === cat)
  );
  renderDocs(filt);
}

function renderDocs(docs) {
  const grid = $('docsGrid');
  if (!grid) return;
  if (!docs.length) {
    grid.innerHTML = '<div style="color:#64748b;padding:40px;text-align:center">Aucun document</div>';
    return;
  }
  grid.innerHTML = docs.map(d => `
    <div class="doc-card" onclick="openDocModal(${d.id})">
      <div class="doc-title">${escHtml(d.title)}</div>
      <div class="doc-preview">${escHtml((d.content||'').substring(0, 120))}</div>
      <div class="doc-meta">
        <span class="doc-cat">${escHtml(d.category)}</span>
        <span class="doc-date">${fmtDate(d.updated_at)}</span>
      </div>
    </div>
  `).join('');
}

// ---------------------------------------------------------------------------
// TEAM
// ---------------------------------------------------------------------------
let allMembers = [];

async function loadTeam() {
  try { allMembers = await api('/mission-control/api/team'); } catch(_) { allMembers = []; }
  renderTeam();
}

function renderTeam() {
  const el = $('teamOrg');
  if (!el) return;
  const main = allMembers.filter(m => m.agent_type === 'main');
  const subs = allMembers.filter(m => m.agent_type !== 'main');

  const cardHtml = (m, big=false) => `
    <div class="team-card ${big ? 'main-agent' : ''}" onclick="openTeamModal(${m.id})">
      <div class="team-status-dot ${m.status}"></div>
      <div class="team-avatar" style="background:${m.color || '#4f8ef7'}">${escHtml(m.emoji || m.name[0])}</div>
      <div class="team-name">${escHtml(m.name)}</div>
      <div class="team-role">${escHtml(m.role)}</div>
      ${m.current_task ? `<div class="team-current-task">${escHtml(m.current_task)}</div>` : ''}
      ${m.mission ? `<div class="team-mission">${escHtml(m.mission.substring(0,100))}${m.mission.length>100?'...':''}</div>` : ''}
    </div>
  `;

  let html = '';
  if (main.length) {
    html += `<div class="team-main">${main.map(m => cardHtml(m, true)).join('')}</div>`;
    if (subs.length) html += `<div class="team-connector">|</div>`;
  }
  if (subs.length) {
    html += `<div class="team-subs">${subs.map(m => cardHtml(m)).join('')}</div>`;
  }
  if (!allMembers.length) {
    html = '<div style="color:#64748b;padding:40px;text-align:center">Aucun agent configure</div>';
  }
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// OFFICE
// ---------------------------------------------------------------------------
let tickerItems = [];

function renderOffice() {
  const floor = $('officeFloor');
  if (!floor) return;
  if (!allMembers.length) {
    floor.innerHTML = '<div style="color:#64748b;padding:40px;text-align:center">Chargement des agents...</div>';
    return;
  }
  floor.innerHTML = allMembers.map(m => `
    <div class="office-desk ${m.status}">
      <div class="desk-status-badge"></div>
      <div class="desk-monitor"><div class="desk-monitor-screen"></div></div>
      <div class="desk-avatar" style="background:${m.color || '#4f8ef7'}">${escHtml(m.emoji || m.name[0])}</div>
      <div class="desk-name">${escHtml(m.name)}</div>
      <div style="font-size:10px;color:#64748b">${escHtml(m.role)}</div>
      <div class="desk-task">${m.current_task ? escHtml(m.current_task) : (m.status === 'working' ? 'En cours...' : 'En attente')}</div>
    </div>
  `).join('');
}

function prependTicker(item) {
  tickerItems.unshift(item);
  if (tickerItems.length > 30) tickerItems.pop();
  const el = $('tickerItems');
  if (!el) return;
  el.innerHTML = tickerItems.map(i => `
    <div class="ticker-item">
      <span class="ta">${escHtml(i.agent)}</span>: ${escHtml(i.action)}
      <div class="tt">${fmtRelative(i.timestamp)}</div>
    </div>
  `).join('');
}

// ---------------------------------------------------------------------------
// TASK MODAL
// ---------------------------------------------------------------------------
let editTaskId = null;

async function openTaskModal(id=null) {
  editTaskId = id;
  $('taskModalTitle').textContent = id ? 'Modifier la tache' : 'Nouvelle tache';
  $('taskId').value = id || '';
  $('taskDeleteBtn').classList.toggle('hidden', !id);

  // Populate project select
  const sel = $('taskProject');
  sel.innerHTML = '<option value="">Aucun</option>' +
    allProjects.map(p => `<option value="${p.id}">${escHtml(p.name)}</option>`).join('');

  if (id) {
    const t = allTasks.find(x => x.id === id);
    if (t) {
      $('taskTitle').value = t.title;
      $('taskDesc').value = t.description || '';
      $('taskStatus').value = t.status;
      $('taskPriority').value = t.priority;
      $('taskAssigned').value = t.assigned_to;
      $('taskProject').value = t.project_id || '';
    }
  } else {
    $('taskTitle').value = '';
    $('taskDesc').value = '';
    $('taskStatus').value = 'todo';
    $('taskPriority').value = 'medium';
    $('taskAssigned').value = 'openclaw';
    $('taskProject').value = '';
  }
  $('taskModal').classList.remove('hidden');
}

async function saveTask() {
  const body = {
    title:       $('taskTitle').value.trim(),
    description: $('taskDesc').value.trim(),
    status:      $('taskStatus').value,
    priority:    $('taskPriority').value,
    assigned_to: $('taskAssigned').value.trim() || 'openclaw',
    project_id:  $('taskProject').value ? parseInt($('taskProject').value) : null,
  };
  if (!body.title) return;
  try {
    if (editTaskId) await api(`/mission-control/api/tasks/${editTaskId}`, 'PUT', body);
    else            await api('/mission-control/api/tasks', 'POST', body);
    closeModal('taskModal');
    await loadBoard();
  } catch(e) { alert('Erreur: ' + e.message); }
}

async function deleteTask() {
  if (!editTaskId || !confirm('Supprimer cette tache ?')) return;
  try {
    await api(`/mission-control/api/tasks/${editTaskId}`, 'DELETE');
    closeModal('taskModal');
    await loadBoard();
  } catch(e) { alert('Erreur: ' + e.message); }
}

// ---------------------------------------------------------------------------
// PROJECT MODAL
// ---------------------------------------------------------------------------
let editProjectId = null;

async function openProjectModal(id=null) {
  editProjectId = id;
  $('projectModalTitle').textContent = id ? 'Modifier le projet' : 'Nouveau projet';
  $('projectId').value = id || '';

  if (id) {
    const p = allProjects.find(x => x.id === id);
    if (p) {
      $('projectName').value = p.name;
      $('projectDesc').value = p.description || '';
      $('projectStatus').value = p.status;
      $('projectProgress').value = p.progress || 0;
    }
  } else {
    $('projectName').value = '';
    $('projectDesc').value = '';
    $('projectStatus').value = 'active';
    $('projectProgress').value = 0;
  }
  $('projectModal').classList.remove('hidden');
}

async function saveProject() {
  const body = {
    name:        $('projectName').value.trim(),
    description: $('projectDesc').value.trim(),
    status:      $('projectStatus').value,
    progress:    parseInt($('projectProgress').value) || 0,
  };
  if (!body.name) return;
  try {
    if (editProjectId) await api(`/mission-control/api/projects/${editProjectId}`, 'PUT', body);
    else               await api('/mission-control/api/projects', 'POST', body);
    closeModal('projectModal');
    await loadProjects();
  } catch(e) { alert('Erreur: ' + e.message); }
}

// ---------------------------------------------------------------------------
// DOC MODAL
// ---------------------------------------------------------------------------
let editDocId = null;

async function openDocModal(id=null) {
  editDocId = id;
  $('docModalTitle').textContent = id ? 'Modifier le document' : 'Nouveau document';
  $('docId').value = id || '';
  $('docDeleteBtn').classList.toggle('hidden', !id);

  if (id) {
    const d = allDocs.find(x => x.id === id);
    if (d) {
      $('docTitle').value = d.title;
      $('docContent').value = d.content || '';
      $('docCategory').value = d.category || 'general';
    }
  } else {
    $('docTitle').value = '';
    $('docContent').value = '';
    $('docCategory').value = 'general';
  }
  $('docModal').classList.remove('hidden');
}

async function saveDoc() {
  const body = {
    title:    $('docTitle').value.trim(),
    content:  $('docContent').value,
    category: $('docCategory').value,
    project_id: null,
  };
  if (!body.title) return;
  try {
    if (editDocId) await api(`/mission-control/api/docs/${editDocId}`, 'PUT', body);
    else           await api('/mission-control/api/docs', 'POST', body);
    closeModal('docModal');
    await loadDocs();
  } catch(e) { alert('Erreur: ' + e.message); }
}

async function deleteDoc() {
  if (!editDocId || !confirm('Supprimer ce document ?')) return;
  try {
    await api(`/mission-control/api/docs/${editDocId}`, 'DELETE');
    closeModal('docModal');
    await loadDocs();
  } catch(e) { alert('Erreur: ' + e.message); }
}

// ---------------------------------------------------------------------------
// TEAM MODAL
// ---------------------------------------------------------------------------
let editMemberId = null;

async function openTeamModal(id=null) {
  editMemberId = id;
  $('teamModalTitle').textContent = id ? 'Modifier l\'agent' : 'Nouvel agent';
  $('teamId').value = id || '';
  $('teamDeleteBtn').classList.toggle('hidden', !id);

  // Populate parent select
  const sel = $('teamParent');
  sel.innerHTML = '<option value="">Aucun</option>' +
    allMembers.filter(m => m.id !== id).map(m => `<option value="${m.id}">${escHtml(m.name)}</option>`).join('');

  if (id) {
    const m = allMembers.find(x => x.id === id);
    if (m) {
      $('teamName').value = m.name;
      $('teamEmoji').value = m.emoji || '?';
      $('teamRole').value = m.role || '';
      $('teamType').value = m.agent_type || 'sub';
      $('teamMission').value = m.mission || '';
      $('teamColor').value = m.color || '#4f8ef7';
      $('teamParent').value = m.parent_id || '';
    }
  } else {
    $('teamName').value = '';
    $('teamEmoji').value = '?';
    $('teamRole').value = '';
    $('teamType').value = 'sub';
    $('teamMission').value = '';
    $('teamColor').value = '#4f8ef7';
    $('teamParent').value = '';
  }
  $('teamModal').classList.remove('hidden');
}

async function saveMember() {
  const body = {
    name:       $('teamName').value.trim(),
    emoji:      $('teamEmoji').value.trim() || '?',
    role:       $('teamRole').value.trim(),
    agent_type: $('teamType').value,
    mission:    $('teamMission').value.trim(),
    color:      $('teamColor').value,
    parent_id:  $('teamParent').value ? parseInt($('teamParent').value) : null,
    status:     'idle',
    current_task: null,
  };
  if (!body.name) return;
  try {
    if (editMemberId) await api(`/mission-control/api/team/${editMemberId}`, 'PUT', body);
    else              await api('/mission-control/api/team', 'POST', body);
    closeModal('teamModal');
    await loadTeam();
    renderOffice();
  } catch(e) { alert('Erreur: ' + e.message); }
}

async function deleteMember() {
  if (!editMemberId || !confirm('Supprimer cet agent ?')) return;
  try {
    await api(`/mission-control/api/team/${editMemberId}`, 'DELETE');
    closeModal('teamModal');
    await loadTeam();
    renderOffice();
  } catch(e) { alert('Erreur: ' + e.message); }
}

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------
function closeModal(id) {
  $(id).classList.add('hidden');
}

// Close modal on overlay click
document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.classList.add('hidden');
  }
});

// ---------------------------------------------------------------------------
// INIT
// ---------------------------------------------------------------------------
async function init() {
  await Promise.all([loadBoard(), loadProjects(), loadTeam()]);
  loadHeaderMetrics();
  setInterval(loadHeaderMetrics, 120000);
  pollHeartbeat();
  setInterval(pollHeartbeat, 30000);
  initSSE();
}

init();
