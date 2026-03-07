/* ================================================================
   VPS Dashboard - app.js
================================================================ */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let allTasks   = [];
let allEvents  = [];
let taskFilter = 'all';
let selectedEventColor = 'blue';
let editingTaskId = null;

// Calendar state
let calYear  = new Date().getFullYear();
let calMonth = new Date().getMonth();

// ---------------------------------------------------------------------------
// Utils
// ---------------------------------------------------------------------------
const $ = id => document.getElementById(id);
const today = () => new Date().toISOString().split('T')[0];

function fmt(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short' });
}

function fmtTime(datetimeStr) {
  if (!datetimeStr) return '';
  const d = new Date(datetimeStr);
  return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function isOverdue(dateStr) {
  return dateStr && dateStr < today();
}

function greetingText() {
  const h = new Date().getHours();
  if (h < 6)  return 'Bonne nuit 🌙';
  if (h < 12) return 'Bonjour ☀️';
  if (h < 18) return 'Bon après-midi 🌤️';
  return 'Bonsoir 🌆';
}

// ---------------------------------------------------------------------------
// Clock & Header
// ---------------------------------------------------------------------------
function updateClock() {
  const now = new Date();
  $('headerTime').textContent = now.toLocaleTimeString('fr-FR', {
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
  $('headerDate').textContent = now.toLocaleDateString('fr-FR', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
  });
  $('headerGreeting').textContent = greetingText();
}

// ---------------------------------------------------------------------------
// Summary chips
// ---------------------------------------------------------------------------
async function loadSummary() {
  try {
    const data = await api('/api/summary');
    const chips = [];
    if (data.tasks_overdue > 0)
      chips.push(`<div class="chip chip-warning">⚠ ${data.tasks_overdue} en retard</div>`);
    if (data.tasks_today > 0)
      chips.push(`<div class="chip chip-info">📋 ${data.tasks_today} aujourd'hui</div>`);
    if (data.events_today > 0)
      chips.push(`<div class="chip chip-info">📅 ${data.events_today} événement(s)</div>`);
    if (data.tasks_done_today > 0)
      chips.push(`<div class="chip chip-success">✓ ${data.tasks_done_today} terminé(s)</div>`);
    $('summaryChips').innerHTML = chips.join('');
  } catch(e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------
async function api(url, method = 'GET', body = null) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------
function toast(msg, type = 'success') {
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  $('toastContainer').appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// ---------------------------------------------------------------------------
// Modals
// ---------------------------------------------------------------------------
function openModal(id) {
  const m = $(id);
  m.classList.add('open');
  // Pre-fill date in task modal
  if (id === 'taskModal' && !editingTaskId) {
    $('taskDate').value = today();
    $('taskId').value = '';
    $('taskTitle').value = '';
    $('taskDesc').value = '';
    $('taskPriority').value = 'medium';
    $('taskStatus').value = 'todo';
    $('taskModalTitle').textContent = 'Nouvelle tâche';
  }
  if (id === 'eventModal') {
    const now = new Date();
    now.setMinutes(0, 0, 0);
    const pad = n => String(n).padStart(2,'0');
    const localISO = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}T${pad(now.getHours())}:00`;
    $('eventStart').value = localISO;
  }
}

function closeModal(id) {
  $(id).classList.remove('open');
  editingTaskId = null;
}

// Close modal on overlay click
document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.remove('open');
  });
});

// ---------------------------------------------------------------------------
// Color pickers
// ---------------------------------------------------------------------------
document.querySelectorAll('#eventModal .color-dot').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#eventModal .color-dot').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    selectedEventColor = btn.dataset.color;
  });
});

// ---------------------------------------------------------------------------
// TASKS
// ---------------------------------------------------------------------------
async function loadTasks() {
  allTasks = await api('/api/tasks');
  renderTasks();
}

function renderTasks() {
  const list = $('tasksList');
  let tasks = [...allTasks];
  const todayStr = today();

  if (taskFilter === 'today') tasks = tasks.filter(t => t.due_date === todayStr);
  else if (taskFilter === 'todo') tasks = tasks.filter(t => t.status !== 'done');
  else if (taskFilter === 'done') tasks = tasks.filter(t => t.status === 'done');

  if (tasks.length === 0) {
    list.innerHTML = `<div class="tasks-empty">Aucune tâche${taskFilter !== 'all' ? ' dans ce filtre' : ''}</div>`;
    return;
  }

  // Group: overdue | today | upcoming | done
  const overdue  = tasks.filter(t => t.status !== 'done' && isOverdue(t.due_date));
  const todayT   = tasks.filter(t => t.status !== 'done' && t.due_date === todayStr);
  const upcoming = tasks.filter(t => t.status !== 'done' && (!t.due_date || t.due_date > todayStr));
  const done     = tasks.filter(t => t.status === 'done');

  let html = '';
  if (overdue.length)  html += `<div class="task-group-label" style="color:var(--red)">En retard</div>` + overdue.map(taskCardHTML).join('');
  if (todayT.length)   html += `<div class="task-group-label">Aujourd'hui</div>` + todayT.map(taskCardHTML).join('');
  if (upcoming.length) html += `<div class="task-group-label">À venir</div>` + upcoming.map(taskCardHTML).join('');
  if (done.length)     html += `<div class="task-group-label">Terminé</div>` + done.map(taskCardHTML).join('');

  list.innerHTML = html;
}

function taskCardHTML(t) {
  const isDone   = t.status === 'done';
  const overdue  = !isDone && isOverdue(t.due_date);
  const dateStr  = t.due_date ? `<span class="task-date ${overdue ? 'overdue' : ''}">${fmt(t.due_date)}</span>` : '';
  return `
    <div class="task-card ${isDone ? 'done' : ''}" id="task-${t.id}">
      <div class="task-check ${isDone ? 'checked' : ''}" onclick="toggleTask(${t.id}, '${t.status}')"></div>
      <div class="task-body" onclick="editTask(${t.id})">
        <div class="task-title">${escHtml(t.title)}</div>
        <div class="task-meta">
          <div class="priority-dot ${t.priority}"></div>
          ${dateStr}
          ${t.status === 'in_progress' ? '<span style="font-size:10px;color:var(--yellow)">En cours</span>' : ''}
        </div>
      </div>
      <div class="task-actions">
        <button class="task-action-btn del" onclick="deleteTask(${t.id})" title="Supprimer">✕</button>
      </div>
    </div>`;
}

async function toggleTask(id, currentStatus) {
  const newStatus = currentStatus === 'done' ? 'todo' : 'done';
  await api(`/api/tasks/${id}`, 'PATCH', { status: newStatus });
  await loadTasks();
  await loadSummary();
}

function editTask(id) {
  const t = allTasks.find(x => x.id === id);
  if (!t) return;
  editingTaskId = id;
  $('taskId').value = id;
  $('taskTitle').value = t.title;
  $('taskDesc').value = t.description || '';
  $('taskPriority').value = t.priority;
  $('taskStatus').value = t.status;
  $('taskDate').value = t.due_date || '';
  $('taskModalTitle').textContent = 'Modifier la tâche';
  openModal('taskModal');
}

async function saveTask() {
  const title = $('taskTitle').value.trim();
  if (!title) { toast('Le titre est obligatoire', 'error'); return; }
  const payload = {
    title,
    description: $('taskDesc').value.trim(),
    priority: $('taskPriority').value,
    status: $('taskStatus').value,
    due_date: $('taskDate').value || null,
  };
  try {
    if (editingTaskId) {
      await api(`/api/tasks/${editingTaskId}`, 'PATCH', payload);
      toast('Tâche modifiée');
    } else {
      await api('/api/tasks', 'POST', payload);
      toast('Tâche créée');
    }
    closeModal('taskModal');
    await loadTasks();
    await loadSummary();
  } catch(e) { toast('Erreur', 'error'); }
}

async function deleteTask(id) {
  await api(`/api/tasks/${id}`, 'DELETE');
  toast('Tâche supprimée');
  await loadTasks();
  await loadSummary();
}

// Filter buttons
document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    taskFilter = btn.dataset.filter;
    renderTasks();
  });
});

// ---------------------------------------------------------------------------
// EVENTS / AGENDA
// ---------------------------------------------------------------------------
async function loadEvents() {
  const [localEvents, googleEvents] = await Promise.all([
    api('/api/events'),
    api('/api/events/google').catch(() => []),
  ]);
  allEvents = [...localEvents, ...googleEvents].sort(
    (a, b) => new Date(a.start_datetime) - new Date(b.start_datetime)
  );
  renderAgendaDay();
  renderMiniCalendar();
}

function renderAgendaDay() {
  const timeline = $('agendaTimeline');
  const todayStr = today();
  const todayEvents = allEvents.filter(e => e.start_datetime.startsWith(todayStr));

  if (!todayEvents.length) {
    timeline.innerHTML = '<div class="agenda-empty">Aucun événement aujourd\'hui</div>';
    return;
  }

  timeline.innerHTML = todayEvents.map(e => {
    const deleteBtn = e.source === 'google'
      ? ''
      : `<button class="event-delete" onclick="deleteEvent(${e.id})">✕</button>`;
    return `
    <div class="agenda-event color-${e.color}">
      <div class="event-time">${fmtTime(e.start_datetime)}</div>
      <div>
        <div class="event-title">${escHtml(e.title)}</div>
        ${e.description ? `<div class="event-desc">${escHtml(e.description)}</div>` : ''}
      </div>
      ${deleteBtn}
    </div>`;
  }).join('');
}

async function saveEvent() {
  const title = $('eventTitle').value.trim();
  const start = $('eventStart').value;
  if (!title) { toast('Titre obligatoire', 'error'); return; }
  if (!start) { toast('Date de début obligatoire', 'error'); return; }
  try {
    await api('/api/events', 'POST', {
      title,
      description: $('eventDesc').value.trim(),
      start_datetime: start,
      end_datetime: $('eventEnd').value || null,
      color: selectedEventColor,
    });
    $('eventTitle').value = '';
    $('eventDesc').value = '';
    toast('Événement créé');
    closeModal('eventModal');
    await loadEvents();
    await loadSummary();
  } catch(e) { toast('Erreur', 'error'); }
}

async function deleteEvent(id) {
  await api(`/api/events/${id}`, 'DELETE');
  toast('Événement supprimé');
  await loadEvents();
  await loadSummary();
}

// ---------------------------------------------------------------------------
// MINI CALENDAR
// ---------------------------------------------------------------------------
function renderMiniCalendar() {
  const container = $('miniCalendar');
  const todayDate = new Date();
  const todayStr  = today();

  // Days with events (local vs Google)
  const localEventDays  = new Set(allEvents.filter(e => e.source !== 'google').map(e => e.start_datetime.split('T')[0]));
  const googleEventDays = new Set(allEvents.filter(e => e.source === 'google').map(e => e.start_datetime.split('T')[0]));

  const firstDay = new Date(calYear, calMonth, 1);
  const lastDay  = new Date(calYear, calMonth + 1, 0);
  const startDow = (firstDay.getDay() + 6) % 7; // Monday first

  const monthName = firstDay.toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });

  let html = `
    <div class="cal-header">
      <button class="cal-nav" onclick="calNav(-1)">‹</button>
      <span class="cal-title">${monthName}</span>
      <button class="cal-nav" onclick="calNav(1)">›</button>
    </div>
    <div class="cal-grid">
      <div class="cal-day-name">Lu</div>
      <div class="cal-day-name">Ma</div>
      <div class="cal-day-name">Me</div>
      <div class="cal-day-name">Je</div>
      <div class="cal-day-name">Ve</div>
      <div class="cal-day-name">Sa</div>
      <div class="cal-day-name">Di</div>`;

  // Empty slots before first day
  for (let i = 0; i < startDow; i++) {
    const prev = new Date(calYear, calMonth, -startDow + i + 1);
    html += `<div class="cal-day other-month">${prev.getDate()}</div>`;
  }

  for (let d = 1; d <= lastDay.getDate(); d++) {
    const dateStr = `${calYear}-${String(calMonth+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const isToday   = dateStr === todayStr;
    const hasLocal  = localEventDays.has(dateStr);
    const hasGoogle = googleEventDays.has(dateStr);
    const classes   = ['cal-day', isToday ? 'today' : '', hasLocal ? 'has-event' : '', hasGoogle ? 'has-google-event' : ''].filter(Boolean).join(' ');
    html += `<div class="${classes}" onclick="calDayClick('${dateStr}')">${d}</div>`;
  }

  // Fill remaining
  const totalCells = startDow + lastDay.getDate();
  const remainder  = totalCells % 7 === 0 ? 0 : 7 - (totalCells % 7);
  for (let i = 1; i <= remainder; i++) {
    html += `<div class="cal-day other-month">${i}</div>`;
  }

  html += '</div>';
  container.innerHTML = html;
}

function calNav(dir) {
  calMonth += dir;
  if (calMonth > 11) { calMonth = 0; calYear++; }
  if (calMonth < 0)  { calMonth = 11; calYear--; }
  renderMiniCalendar();
}

function calDayClick(dateStr) {
  // Pre-fill event modal with clicked date
  $('eventStart').value = dateStr + 'T09:00';
  openModal('eventModal');
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function escHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function fmtDateTime(dtStr) {
  if (!dtStr) return '';
  const d = new Date(dtStr);
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
    editingTaskId = null;
  }
  // Ctrl+N = new task
  if (e.ctrlKey && e.key === 'n') {
    e.preventDefault();
    openModal('taskModal');
  }
});

// ---------------------------------------------------------------------------
// TRADING (Notion STM)
// ---------------------------------------------------------------------------
let tradeTab = 'open';
let allStm = [];
let allWatchlist = [];

async function loadTrading() {
  const [stm, wl] = await Promise.all([
    api('/api/notion/stm').catch(() => []),
    api('/api/notion/watchlist').catch(() => []),
  ]);
  allStm = stm;
  allWatchlist = wl;
  renderTrading();
}

function switchTradeTab(tab) {
  tradeTab = tab;
  document.querySelectorAll('.trade-tab').forEach(b => b.classList.remove('active'));
  document.querySelector(`.trade-tab[data-tab="${tab}"]`)?.classList.add('active');
  const syncBtn = $('ibkrSyncBtn');
  if (syncBtn) syncBtn.style.display = tab === 'ibkr' ? '' : 'none';
  renderTrading();
}

function renderTrading() {
  const el = $('tradingContent');
  if (!el) return;

  if (tradeTab === 'ibkr') {
    renderIbkr();
    return;
  }

  if (tradeTab === 'watchlist') {
    if (!allWatchlist.length) {
      el.innerHTML = '<div class="trading-empty">Watchlist vide</div>';
      return;
    }
    el.innerHTML = allWatchlist.map(w => `
      <a class="watch-row" href="${w.url}" target="_blank" rel="noopener">
        <span class="watch-name">${escHtml(w.nom || '—')}</span>
        ${w.etat ? `<span class="watch-etat">${escHtml(w.etat)}</span>` : ''}
      </a>`).join('');
    return;
  }

  const trades = tradeTab === 'open'
    ? allStm.filter(t => !t.sortie)
    : allStm.slice(0, 20);

  if (!trades.length) {
    el.innerHTML = `<div class="trading-empty">${tradeTab === 'open' ? 'Aucune position live' : 'Aucun trade'}</div>`;
    return;
  }

  el.innerHTML = trades.map(t => {
    const pnl = t.pnl ?? null;
    const pnlPct = t.pnl_pct ?? null;
    const pnlClass = pnl === null ? 'neutral' : pnl >= 0 ? 'pos' : 'neg';
    const pnlStr = pnl !== null
      ? `${pnl >= 0 ? '+' : ''}$${Math.abs(pnl).toFixed(0)}`
      : '—';
    const pnlPctStr = pnlPct !== null
      ? `${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`
      : '';
    const rr = t.rr !== null ? `RR ${t.rr?.toFixed(2)}` : '';
    const entree = t.entree ? fmt(t.entree) : '—';
    const liveBadge = t.live_days !== null && tradeTab === 'open'
      ? `<span class="trade-live-badge">${t.live_days}j</span>`
      : '';
    return `
      <a class="trade-row" href="${t.url}" target="_blank" rel="noopener">
        <span class="trade-ticker">${escHtml(t.nom || '—')}</span>
        <div class="trade-info">
          <span class="trade-meta">Entrée ${entree}${t.sl ? ` · SL ${t.sl}` : ''}</span>
          ${rr ? `<span class="trade-rr">${rr}</span>` : ''}
        </div>
        ${liveBadge}
        <div>
          <div class="trade-pnl ${pnlClass}">${pnlStr}</div>
          ${pnlPctStr ? `<div class="trade-rr">${pnlPctStr}</div>` : ''}
        </div>
      </a>`;
  }).join('');
}

// ---------------------------------------------------------------------------
// IBKR
// ---------------------------------------------------------------------------
let ibkrPerf = null;
let ibkrExpandedMonth = null;
let ibkrMonthTrades = {};

async function loadIbkr() {
  try {
    ibkrPerf = await api('/api/ibkr/perf');
  } catch(e) { ibkrPerf = null; }
  renderIbkrChip();
  renderIbkrTable();
}

async function syncIbkr() {
  const btns = [$('ibkrSyncBtn'), $('ibkrSyncBtnTable')].filter(Boolean);
  btns.forEach(b => { b.disabled = true; b.textContent = '…'; });
  try {
    const res = await api('/api/ibkr/sync', 'POST', {});
    toast(`IBKR: ${res.fetched} trades (${res.inserted} nouveaux)`, 'success');
    ibkrMonthTrades = {};
    await loadIbkr();
    if (tradeTab === 'ibkr') renderTrading();
  } catch(e) {
    toast('Erreur sync IBKR', 'error');
  } finally {
    btns.forEach(b => { b.disabled = false; b.textContent = '↻'; });
  }
}

function renderIbkrChip() {
  const chips = $('summaryChips');
  if (!chips) return;
  const old = chips.querySelector('.chip-ibkr');
  if (old) old.remove();
  if (!ibkrPerf || !ibkrPerf.ytd) return;
  const ytd = ibkrPerf.ytd;
  if (!ytd.trades) return;
  const net = ytd.pnl_net;
  const sign = net >= 0 ? '+' : '';
  const cls  = net >= 0 ? 'chip-success' : 'chip-danger';
  const chip = document.createElement('div');
  chip.className = `chip ${cls} chip-ibkr`;
  chip.innerHTML = `YTD <strong>${sign}$${Math.abs(net).toFixed(0)}</strong> · ${ytd.win_rate}%`;
  chips.prepend(chip);
}

function renderIbkrTable() {
  const container = $('ibkrTableContainer');
  const syncLabel = $('ibkrSectionSync');
  if (!container) return;

  if (!ibkrPerf) {
    container.innerHTML = '<div class="ibkr-tbl-empty">Aucune donnée — cliquez ↻ pour synchroniser</div>';
    if (syncLabel) syncLabel.textContent = '';
    return;
  }

  if (syncLabel && ibkrPerf.last_sync) {
    syncLabel.textContent = 'Sync: ' + new Date(ibkrPerf.last_sync)
      .toLocaleDateString('fr-FR', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' });
  }

  if (!ibkrPerf.monthly.length) {
    container.innerHTML = '<div class="ibkr-tbl-empty">Aucun trade enregistré</div>';
    return;
  }

  let html = `<table class="ibkr-tbl">
    <thead>
      <tr>
        <th>Mois</th><th class="num">Trades</th><th class="num">Win%</th>
        <th class="num">PnL brut</th><th class="num">Comm</th><th class="num">PnL net</th>
      </tr>
    </thead>
    <tbody>`;

  for (const m of ibkrPerf.monthly) {
    const netCls = m.pnl_net >= 0 ? 'pos' : 'neg';
    const netSign = m.pnl_net >= 0 ? '+' : '';
    const brutSign = m.pnl >= 0 ? '+' : '';
    const brutCls = m.pnl >= 0 ? 'pos' : 'neg';
    const [yr, mo] = m.month.split('-');
    const label = new Date(Number(yr), Number(mo) - 1, 1)
      .toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
    const isOpen = ibkrExpandedMonth === m.month;

    html += `<tr class="ibkr-tbl-month ${isOpen ? 'open' : ''}" onclick="toggleIbkrMonth('${m.month}')">
      <td class="ibkr-tbl-month-name"><span class="ibkr-tbl-chevron">${isOpen ? '▾' : '▸'}</span>${label}</td>
      <td class="num">${m.trades}</td>
      <td class="num">${m.win_rate}%</td>
      <td class="num ${brutCls}">${brutSign}$${Math.abs(m.pnl).toFixed(0)}</td>
      <td class="num comm">-$${Math.abs(m.commission).toFixed(0)}</td>
      <td class="num ${netCls} bold">${netSign}$${Math.abs(m.pnl_net).toFixed(0)}</td>
    </tr>`;

    if (isOpen) {
      const trades = ibkrMonthTrades[m.month] || [];
      if (!trades.length) {
        html += `<tr class="ibkr-tbl-trades-row"><td colspan="6" class="ibkr-tbl-loading">Chargement…</td></tr>`;
      } else {
        html += `<tr class="ibkr-tbl-trades-row"><td colspan="6" style="padding:0">
          <table class="ibkr-tbl-sub">
            <thead><tr>
              <th>Date</th><th>B/V</th><th>Symbole</th>
              <th class="num">Qté</th><th class="num">Prix</th>
              <th class="num">PnL réalisé</th><th class="num">Comm</th>
            </tr></thead>
            <tbody>`;
        for (const t of trades) {
          const tCls   = t.pnl > 0 ? 'pos' : t.pnl < 0 ? 'neg' : '';
          const tSign  = t.pnl >= 0 ? '+' : '';
          const bsCls  = t.buy_sell === 'BUY' ? 'buy' : 'sell';
          html += `<tr>
            <td>${t.trade_date || '—'}</td>
            <td><span class="ibkr-trade-bs ${bsCls}">${t.buy_sell === 'BUY' ? 'Achat' : 'Vente'}</span></td>
            <td class="bold">${escHtml(t.symbol || '—')}</td>
            <td class="num">${t.quantity}</td>
            <td class="num">$${t.price.toFixed(2)}</td>
            <td class="num ${tCls}">${t.pnl !== 0 ? `${tSign}$${Math.abs(t.pnl).toFixed(2)}` : '—'}</td>
            <td class="num comm">-$${Math.abs(t.commission).toFixed(2)}</td>
          </tr>`;
        }
        html += `</tbody></table></td></tr>`;
      }
    }
  }

  html += '</tbody></table>';
  container.innerHTML = html;
}

async function toggleIbkrMonth(month) {
  if (ibkrExpandedMonth === month) {
    ibkrExpandedMonth = null;
    renderIbkrTable();
    return;
  }
  ibkrExpandedMonth = month;
  renderIbkrTable(); // show loading state immediately
  if (!ibkrMonthTrades[month]) {
    try {
      ibkrMonthTrades[month] = await api(`/api/ibkr/trades?month=${month}&limit=100`);
    } catch(e) { ibkrMonthTrades[month] = []; }
  }
  renderIbkrTable();
}

function fmtPnl(val, currency) {
  if (val === null || val === undefined) return '—';
  const sign = val >= 0 ? '+' : '';
  const sym = currency === 'EUR' ? '€' : '$';
  return `${sign}${sym}${Math.abs(val).toFixed(0)}`;
}

function renderIbkr() {
  const el = $('tradingContent');
  if (!ibkrPerf || !ibkrPerf.ytd) {
    el.innerHTML = '<div class="trading-empty">Données IBKR non disponibles<br><small>Cliquez ↻ pour synchro</small></div>';
    return;
  }
  const ytd = ibkrPerf.ytd;
  const pnlClass = ytd.pnl_net >= 0 ? 'pos' : 'neg';
  const pnlSign  = ytd.pnl_net >= 0 ? '+' : '';
  const lastSync = ibkrPerf.last_sync
    ? new Date(ibkrPerf.last_sync).toLocaleDateString('fr-FR', { day:'2-digit', month:'short', hour:'2-digit', minute:'2-digit' })
    : 'jamais';

  // Current month quick view
  const curMonth = new Date().toISOString().slice(0, 7);
  const cm = ibkrPerf.monthly.find(m => m.month === curMonth);
  const cmHtml = cm ? (() => {
    const s = cm.pnl_net >= 0 ? '+' : '';
    const c = cm.pnl_net >= 0 ? 'pos' : 'neg';
    const [yr, mo] = cm.month.split('-');
    const lbl = new Date(Number(yr), Number(mo)-1, 1).toLocaleDateString('fr-FR', { month: 'long' });
    return `<div class="ibkr-cm">
      <span class="ibkr-cm-label">${lbl}</span>
      <span class="ibkr-cm-pnl ${c}">${s}$${Math.abs(cm.pnl_net).toFixed(0)}</span>
      <span class="ibkr-cm-stats">${cm.trades}T · ${cm.win_rate}%</span>
    </div>`;
  })() : '';

  el.innerHTML = `
    <div class="ibkr-ytd">
      <div class="ibkr-ytd-row">
        <span class="ibkr-ytd-label">YTD net</span>
        <span class="ibkr-ytd-pnl ${pnlClass}">${pnlSign}$${Math.abs(ytd.pnl_net).toFixed(0)}</span>
      </div>
      <div class="ibkr-ytd-stats">
        <span>${ytd.trades} trades</span>
        <span class="ibkr-sep">·</span>
        <span>${ytd.win_rate}% win</span>
        <span class="ibkr-sep">·</span>
        <span class="ibkr-comm">-$${Math.abs(ytd.commission).toFixed(0)} comm</span>
      </div>
      <div class="ibkr-sync-ts">Sync: ${lastSync}</div>
    </div>
    ${cmHtml}
    <div class="ibkr-tbl-hint">↓ Tableau complet en bas de page</div>`;
}

// ---------------------------------------------------------------------------
// TELEGRAM
// ---------------------------------------------------------------------------
async function sendTelegramDigest() {
  const btn   = $('tgBtn');
  const label = $('tgBtnLabel');
  btn.disabled = true;
  label.textContent = '…';
  try {
    await api('/api/telegram/digest', 'POST', {});
    label.textContent = 'Envoyé ✓';
    btn.classList.add('tg-sent');
    setTimeout(() => { label.textContent = 'Digest'; btn.classList.remove('tg-sent'); btn.disabled = false; }, 3000);
  } catch(e) {
    label.textContent = 'Erreur';
    btn.classList.add('tg-error');
    setTimeout(() => { label.textContent = 'Digest'; btn.classList.remove('tg-error'); btn.disabled = false; }, 3000);
  }
}

async function checkTelegramStatus() {
  try {
    const s = await api('/api/telegram/status');
    const btn = $('tgBtn');
    if (!s.connected) {
      btn.title = 'Telegram non configuré — ajouter TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID dans .env';
      btn.classList.add('tg-disabled');
      btn.disabled = true;
    }
  } catch(e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Google Calendar status
// ---------------------------------------------------------------------------
async function checkGoogleStatus() {
  try {
    const status = await api('/api/google/status');
    const btn   = $('gcalStatusBtn');
    const label = $('gcalStatusLabel');
    if (status.connected) {
      btn.classList.add('gcal-connected');
      btn.removeAttribute('href');
      btn.style.cursor = 'default';
      label.textContent = 'Connecté';
    } else {
      btn.classList.remove('gcal-connected');
      btn.setAttribute('href', '/auth/google');
      label.textContent = 'Connecter';
    }
  } catch(e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// API USAGE PANEL
// ---------------------------------------------------------------------------
async function loadUsage() {
  try {
    const data = await api('/api/usage');
    const el = $('apiUsageList');
    let html = '';

    // --- OpenRouter -------------------------------------------------------
    if (data.openrouter?.connected) {
      const used  = Number(data.openrouter.total_usage);
      const total = Number(data.openrouter.total_credits);
      const rem   = Number(data.openrouter.remaining);
      const pct   = data.openrouter.pct_used;
      const over  = rem < 0;
      const warn  = pct >= 90;
      const barW  = Math.min(pct, 100);
      const dotCls = over ? 'err' : warn ? 'warn' : 'ok';
      const rowCls = over ? 'api-row-err' : warn ? 'api-row-warn' : '';
      const valStr = over
        ? `-$${Math.abs(rem).toFixed(2)} dépassé`
        : `$${rem.toFixed(2)} restants`;
      html += `
        <div class="api-row ${rowCls}">
          <div class="api-row-head">
            <span class="api-dot ${dotCls}"></span>
            <span class="api-name">OpenRouter</span>
            <span class="api-val">${valStr}</span>
          </div>
          <div class="api-bar-track">
            <div class="api-bar-fill ${over ? 'fill-warn' : warn ? 'fill-warn' : ''}" style="width:${barW}%"></div>
          </div>
          <div class="api-sub">$${used.toFixed(2)} utilisés / $${total.toFixed(2)} total (${pct}%)</div>
        </div>`;
    } else {
      html += `
        <div class="api-row api-row-err">
          <div class="api-row-head">
            <span class="api-dot err"></span>
            <span class="api-name">OpenRouter</span>
            <span class="api-val">${data.openrouter?.missing_key ? 'Clé manquante' : 'Erreur'}</span>
          </div>
        </div>`;
    }

    // --- Claude Pro -------------------------------------------------------
    html += `
      <a class="api-row api-row-link" href="https://claude.ai/settings/limits" target="_blank" rel="noopener">
        <div class="api-row-head">
          <span class="api-dot ok"></span>
          <span class="api-name">Claude Pro</span>
          <span class="api-val">Voir usage ↗</span>
        </div>
      </a>`;

    // --- Google -----------------------------------------------------------
    const gOk = data.google?.connected;
    html += `
      <div class="api-row ${gOk ? '' : 'api-row-err'}">
        <div class="api-row-head">
          <span class="api-dot ${gOk ? 'ok' : 'err'}"></span>
          <span class="api-name">Google Calendar</span>
          <span class="api-val">${gOk ? 'OAuth actif' : 'Non connecté'}</span>
        </div>
      </div>`;

    // --- Notion -----------------------------------------------------------
    const nOk = !!data.notion?.connected;
    html += `
      <div class="api-row ${nOk ? '' : 'api-row-err'}">
        <div class="api-row-head">
          <span class="api-dot ${nOk ? 'ok' : 'err'}"></span>
          <span class="api-name">Notion</span>
          <span class="api-val">${nOk ? 'Connecté' : 'Non configuré'}</span>
        </div>
      </div>`;

    // --- Gemini -----------------------------------------------------------
    const gem = data.gemini;
    if (gem?.connected) {
      html += `
        <a class="api-row api-row-link" href="https://aistudio.google.com/usage?timeRange=this-month" target="_blank" rel="noopener">
          <div class="api-row-head">
            <span class="api-dot ok"></span>
            <span class="api-name">Gemini API</span>
            <span class="api-val">Usage ↗</span>
          </div>
        </a>`;
    } else {
      const gemVal = gem?.missing_key ? 'Clé manquante' : 'Erreur';
      html += `
        <div class="api-row api-row-err">
          <div class="api-row-head">
            <span class="api-dot err"></span>
            <span class="api-name">Gemini API</span>
            <span class="api-val">${gemVal}</span>
          </div>
        </div>`;
    }

    el.innerHTML = html;
  } catch(e) { /* ignore */ }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init() {
  updateClock();
  setInterval(updateClock, 1000);

  await Promise.all([
    loadTasks(),
    loadEvents(),
    loadSummary(),
    loadTrading(),
    loadIbkr(),
    loadIbkrRaw(),
  ]);

  // Refresh summary every minute
  setInterval(loadSummary, 60_000);
  // Refresh API usage every 5 min
  setInterval(loadUsage, 5 * 60_000);
  checkGoogleStatus();
  checkTelegramStatus();
  loadUsage();
}

// ---------------------------------------------------------------------------
// IBKR Raw trades table
// ---------------------------------------------------------------------------
let ibkrRawTrades = null;

async function loadIbkrRaw() {
  try {
    ibkrRawTrades = await api('/api/ibkr/trades?limit=500');
  } catch(e) { ibkrRawTrades = []; }
  renderIbkrRaw();
}

function renderIbkrRaw() {
  const container = $('ibkrRawTableContainer');
  const countEl   = $('ibkrRawCount');
  if (!container) return;

  if (!ibkrRawTrades || !ibkrRawTrades.length) {
    container.innerHTML = '<div class="ibkr-tbl-empty">Aucun trade enregistré</div>';
    if (countEl) countEl.textContent = '';
    return;
  }

  if (countEl) countEl.textContent = `${ibkrRawTrades.length} lignes`;

  let html = `<table class="ibkr-tbl ibkr-raw-tbl">
    <thead>
      <tr>
        <th>Date</th>
        <th>Symbole</th>
        <th>Catégorie</th>
        <th>B/V</th>
        <th class="num">Quantité</th>
        <th class="num">Prix</th>
        <th class="num">Produit</th>
        <th class="num">Commission</th>
        <th class="num">PnL réalisé</th>
      </tr>
    </thead>
    <tbody>`;

  for (const t of ibkrRawTrades) {
    const pnlCls  = t.pnl > 0 ? 'pos' : t.pnl < 0 ? 'neg' : '';
    const pnlSign = t.pnl >= 0 ? '+' : '';
    const bsCls   = t.buy_sell === 'BUY' ? 'buy' : 'sell';
    html += `<tr>
      <td>${t.trade_date || '—'}</td>
      <td class="bold">${escHtml(t.symbol || '—')}</td>
      <td>${escHtml(t.asset_category || '—')}</td>
      <td><span class="ibkr-trade-bs ${bsCls}">${t.buy_sell === 'BUY' ? 'Achat' : 'Vente'}</span></td>
      <td class="num">${t.quantity != null ? t.quantity : '—'}</td>
      <td class="num">${t.price != null ? '$' + t.price.toFixed(2) : '—'}</td>
      <td class="num">${t.proceeds != null ? '$' + t.proceeds.toFixed(2) : '—'}</td>
      <td class="num comm">${t.commission != null ? '-$' + Math.abs(t.commission).toFixed(2) : '—'}</td>
      <td class="num ${pnlCls}">${t.pnl !== 0 && t.pnl != null ? `${pnlSign}$${Math.abs(t.pnl).toFixed(2)}` : '—'}</td>
    </tr>`;
  }

  html += '</tbody></table>';
  container.innerHTML = html;
}

init();
