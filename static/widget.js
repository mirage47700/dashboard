// ============================================================
// Dashboard Widget — Scriptable (iOS)
// ============================================================
// Setup:
//   1. Install Scriptable from the App Store
//   2. Create a new script, paste this code
//   3. Set DASHBOARD_URL to your VPS URL (or localhost via Tailscale)
//   4. Add a Scriptable widget on your home screen
//      → choose this script, set size to "medium"
// ============================================================

const DASHBOARD_URL = "https://your-dashboard-url.com"  // ← change this

// ---- Fetch data ------------------------------------------------
async function fetchAll() {
  const [summary, usage, tasks] = await Promise.all([
    fetch(`${DASHBOARD_URL}/api/summary`).then(r => r.json()).catch(() => null),
    fetch(`${DASHBOARD_URL}/api/usage`).then(r => r.json()).catch(() => null),
    fetch(`${DASHBOARD_URL}/api/tasks`).then(r => r.json()).catch(() => []),
  ])
  return { summary, usage, tasks }
}

// ---- Colors ----------------------------------------------------
const C = {
  bg:      new Color("#0d0f14"),
  panel:   new Color("#13161e"),
  accent:  new Color("#6366f1"),
  green:   new Color("#22c55e"),
  yellow:  new Color("#eab308"),
  red:     new Color("#ef4444"),
  text:    new Color("#e2e8f0"),
  muted:   new Color("#64748b"),
  dim:     new Color("#94a3b8"),
}

// ---- Build widget ----------------------------------------------
async function buildWidget() {
  const { summary, usage, tasks } = await fetchAll()

  const w = new ListWidget()
  w.backgroundColor = C.bg
  w.setPadding(14, 16, 14, 16)
  w.url = DASHBOARD_URL

  // --- Header ---------------------------------------------------
  const header = w.addStack()
  header.layoutHorizontally()
  header.centerAlignContent()

  const title = header.addText("Dashboard")
  title.font = Font.boldSystemFont(14)
  title.textColor = C.text

  header.addSpacer()

  const now = new Date()
  const timeStr = now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
  const timeEl = header.addText(timeStr)
  timeEl.font = Font.monospacedSystemFont(12)
  timeEl.textColor = C.muted

  w.addSpacer(8)

  // --- Summary chips --------------------------------------------
  if (summary) {
    const chips = w.addStack()
    chips.layoutHorizontally()
    chips.spacing = 6

    if (summary.tasks_overdue > 0) addChip(chips, `⚠ ${summary.tasks_overdue} retard`, C.yellow)
    if (summary.tasks_today > 0)   addChip(chips, `📋 ${summary.tasks_today} tâches`, C.accent)
    if (summary.events_today > 0)  addChip(chips, `📅 ${summary.events_today} events`, C.dim)
    chips.addSpacer()
  }

  w.addSpacer(10)

  // --- Next tasks -----------------------------------------------
  const urgent = tasks
    .filter(t => t.status !== "done")
    .slice(0, 3)

  for (const t of urgent) {
    const row = w.addStack()
    row.layoutHorizontally()
    row.centerAlignContent()
    row.spacing = 6

    const dot = row.addText(t.priority === "high" ? "●" : "○")
    dot.font = Font.systemFont(10)
    dot.textColor = t.priority === "high" ? C.red : t.priority === "medium" ? C.yellow : C.green

    const label = row.addText(t.title)
    label.font = Font.systemFont(12)
    label.textColor = C.text
    label.lineLimit = 1
    row.addSpacer()

    if (t.due_date) {
      const due = row.addText(t.due_date.slice(5))  // MM-DD
      due.font = Font.systemFont(10)
      due.textColor = t.due_date < new Date().toISOString().slice(0, 10) ? C.red : C.muted
    }

    w.addSpacer(4)
  }

  w.addSpacer()

  // --- Footer: OpenRouter credits --------------------------------
  if (usage?.openrouter?.connected) {
    const or = usage.openrouter
    const footer = w.addStack()
    footer.layoutHorizontally()
    footer.centerAlignContent()

    const orText = footer.addText(`OpenRouter $${or.remaining?.toFixed(2)} restants`)
    orText.font = Font.systemFont(10)
    orText.textColor = or.pct_used >= 90 ? C.yellow : C.muted
    footer.addSpacer()

    const orPct = footer.addText(`${or.pct_used}%`)
    orPct.font = Font.boldSystemFont(10)
    orPct.textColor = or.pct_used >= 90 ? C.yellow : C.dim
  }

  return w
}

// ---- Helper ----------------------------------------------------
function addChip(stack, text, color) {
  const chip = stack.addStack()
  chip.backgroundColor = new Color(color.hex, 0.15)
  chip.cornerRadius = 6
  chip.setPadding(3, 7, 3, 7)
  const t = chip.addText(text)
  t.font = Font.boldSystemFont(10)
  t.textColor = color
}

// ---- Run -------------------------------------------------------
const widget = await buildWidget()

if (config.runsInWidget) {
  Script.setWidget(widget)
} else {
  widget.presentMedium()
}
Script.complete()
