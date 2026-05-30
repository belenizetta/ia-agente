'use strict';

// ── Estado global ──────────────────────────────────────────────────
let jobId       = null;
let pollTimer   = null;
let seenEvents  = 0;
let repoIdx     = 1;

const HEADERS = { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': 'true' };
const GET_HDR  = { 'ngrok-skip-browser-warning': 'true' };

const EVENT_LABELS = {
  job_start:             'Análisis iniciado',
  repo_clone:            'Repositorio clonado',
  repo_reuse:            'Repositorio disponible localmente',
  routing_info:          'Servicios afectados detectados',
  project_info:          'Lenguajes y frameworks detectados',
  intent_info:           'Intención interpretada',
  plan:                  'Plan generado',
  global_context_mapped: 'Archivos del plan leídos',
  files_selected:        'Archivos seleccionados',
  generated_preview:     'Código generado — revisá los cambios',
  task_start:            'Ejecutando tarea',
  generated_changes:     'Cambios aplicados',
  task_end:              'Tarea finalizada',
  job_end:               'Proceso finalizado',
};

// ── Repos dinámicos ────────────────────────────────────────────────
function addRepo() {
  const list = document.getElementById('reposList');
  const div  = document.createElement('div');
  div.className = 'repo-entry';
  div.dataset.idx = repoIdx++;
  div.innerHTML = `
    <input class="input" placeholder="nombre" data-field="name">
    <input class="input" placeholder="URL del repo" data-field="url">
    <input class="input" placeholder="Token" data-field="token" type="password">
    <button type="button" class="btn-remove" onclick="this.closest('.repo-entry').remove()">✕</button>`;
  list.appendChild(div);
}

function getRepos() {
  const repos = {}, tokens = {};
  document.querySelectorAll('.repo-entry').forEach(row => {
    const name  = row.querySelector('[data-field="name"]').value.trim();
    const url   = row.querySelector('[data-field="url"]').value.trim();
    const token = row.querySelector('[data-field="token"]').value.trim();
    if (name && url) { repos[name] = url; if (token) tokens[name] = token; }
  });
  return { repos, tokens };
}

// ── Submit ─────────────────────────────────────────────────────────
async function handleSubmit(e) {
  e.preventDefault();

  const prompt = document.getElementById('prompt').value.trim();
  if (!prompt) { alert('Ingresá un prompt.'); return; }

  const { repos, tokens } = getRepos();
  if (!Object.keys(repos).length) { alert('Ingresá al menos un repositorio.'); return; }

  const baseBranch = document.getElementById('baseBranch').value.trim() || 'main';

  setBtnLoading(true);
  showOnly('logCard');
  clearLog('logEntries');
  setText('logTitle', 'Analizando repositorio...');
  spinner('logSpinner', true);

  try {
    const r = await fetch('/process', {
      method: 'POST', headers: HEADERS,
      body: JSON.stringify({ prompt, repos, tokens, base_branch: baseBranch }),
    });
    const data = await r.json();
    jobId = data.job_id;
    logEntry('logEntries', 'Job iniciado: ' + jobId, 'info');
    startPoll('logEntries', 'pending_approval', onPlanReady);
  } catch (err) {
    logEntry('logEntries', 'Error: ' + err.message, 'error');
    setBtnLoading(false);
  }
}

// ── Polling ────────────────────────────────────────────────────────
function startPoll(logId, targetStatus, onDone) {
  seenEvents = 0;
  stopPoll();
  pollTimer = setInterval(async () => {
    try {
      const r = await fetch(`/jobs/${jobId}/status`, { headers: GET_HDR });
      const d = await r.json();
      const evs = d.all_events || [];

      for (let i = seenEvents; i < evs.length; i++) {
        const ev    = evs[i];
        const label = EVENT_LABELS[ev.event] || ev.event;
        let extra   = '';

        if (ev.event === 'routing_info')
          extra = ' — ' + (ev.data?.affected_services || []).join(', ');
        else if (ev.event === 'project_info')
          extra = ' — ' + [...(ev.data?.languages||[]), ...(ev.data?.frameworks||[])].join(', ');
        else if (ev.event === 'files_selected')
          extra = ' — ' + (ev.data?.files||[]).map(f => f.split(/[\\/]/).pop()).join(', ');
        else if (ev.event === 'generated_preview')
          extra = ` — ${ev.data?.diffs_count || 0} archivo(s)`;
        else if (ev.event === 'task_start')
          extra = ` [${ev.data?.service} → ${ev.data?.action}]`;
        else if (ev.event === 'task_end')
          extra = ` [${ev.data?.status}]`;

        logEntry(logId, label + extra, 'done');
      }
      seenEvents = evs.length;

      const done = ['done', 'ok', 'error', 'rejected'].includes(d.status) || d.status === targetStatus;
      if (done) {
        stopPoll();
        if (d.status === 'error') {
          const errEv = [...evs].reverse().find(e => e.event === 'job_end');
          logEntry(logId, 'Error: ' + (errEv?.data?.message || 'desconocido'), 'error');
          setBtnLoading(false);
        } else {
          onDone(d);
        }
      }
    } catch (_) { /* continuar */ }
  }, 2000);
}

function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

// ── Callbacks ──────────────────────────────────────────────────────
async function onPlanReady(statusData) {
  spinner('logSpinner', false);
  setText('logTitle', 'Análisis completado');

  try {
    const r = await fetch(`/jobs/${jobId}/preview`, { headers: GET_HDR });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const preview = await r.json();
    renderPlan(preview);
    show('planSection');
  } catch (err) {
    logEntry('logEntries', 'Error cargando el plan: ' + err.message, 'error');
    setBtnLoading(false);
  }
}

async function applyChanges() {
  if (!jobId) return;
  document.getElementById('applyBtn').disabled = true;
  hide('planSection');
  showOnly('execCard', false);
  clearLog('execEntries');
  setText('execTitle', 'Aplicando cambios...');
  spinner('execSpinner', true);

  try {
    await fetch('/confirm', {
      method: 'POST', headers: HEADERS,
      body: JSON.stringify({ job_id: jobId, approved: true }),
    });
    startPoll('execEntries', 'done', onDone);
  } catch (err) {
    logEntry('execEntries', 'Error: ' + err.message, 'error');
  }
}

async function rejectPlan() {
  if (!jobId) return;
  try {
    await fetch('/confirm', {
      method: 'POST', headers: HEADERS,
      body: JSON.stringify({ job_id: jobId, approved: false }),
    });
  } catch (_) {}
  resetApp();
}

function onDone(statusData) {
  spinner('execSpinner', false);
  setText('execTitle', 'Aplicado');
  const evs    = statusData.all_events || [];
  const jobEnd = [...evs].reverse().find(e => e.event === 'job_end');
  renderResults(jobEnd?.data?.results || []);
  hide('execCard');
  show('resultCard');
  setBtnLoading(false);
}

function resetApp() {
  jobId = null; stopPoll();
  hide('logCard'); hide('planSection'); hide('execCard'); hide('resultCard');
  show('emptyState');
  setBtnLoading(false);
}

// ── Renderizado del plan ───────────────────────────────────────────
function renderPlan(preview) {
  setText('planSummary', preview.summary || '');
  const container = document.getElementById('diffsList');
  container.innerHTML = '';

  // Mostrar análisis si el LLM respondió preguntas sin generar cambios
  const analysis = preview.analysis || [];
  if (analysis.length) {
    analysis.forEach(a => container.appendChild(buildAnalysisCard(a)));
  }

  const diffs = preview.diffs || [];
  if (!diffs.length && !analysis.length) {
    container.innerHTML = '<p style="color:var(--muted);font-size:13px">El LLM no generó cambios detectables. Intentá con un prompt más específico.</p>';
    return;
  }
  diffs.forEach((d, i) => container.appendChild(buildDiffCard(d, i)));
}

function buildAnalysisCard(a) {
  const card = el('div', 'diff-card');
  card.style.cssText = 'border-left: 3px solid var(--accent)';

  const header = el('div', 'diff-card-header');
  header.innerHTML = `
    <span class="diff-tag">${esc(a.service)}</span>
    <span class="diff-path" title="${esc(a.file)}">${esc(a.file)}</span>
    <span style="color:var(--accent);font-size:12px;margin-left:auto">📋 Análisis</span>
    <span class="diff-toggle">▾</span>`;

  const body = el('div', 'diff-body');
  body.style.cssText = 'padding:16px;white-space:pre-wrap;font-family:var(--mono);font-size:13px;line-height:1.6;color:var(--fg)';
  body.textContent = a.text || '(sin respuesta)';

  header.addEventListener('click', () => {
    const collapsed = body.classList.toggle('collapsed');
    header.querySelector('.diff-toggle').textContent = collapsed ? '▸' : '▾';
  });

  card.appendChild(header);
  card.appendChild(body);
  return card;
}

function buildDiffCard(diff, idx) {
  const adds    = diff.diff_lines.filter(l => l.startsWith('+') && !l.startsWith('+++')).length;
  const dels    = diff.diff_lines.filter(l => l.startsWith('-') && !l.startsWith('---')).length;
  const fname   = diff.path.split(/[\\/]/).pop();

  const card   = el('div', 'diff-card');
  const header = el('div', 'diff-card-header');
  header.innerHTML = `
    <span class="diff-tag">${esc(diff.service)}</span>
    <span class="diff-path" title="${esc(diff.path)}">${esc(diff.path)}</span>
    <span class="diff-stats"><span class="a">+${adds}</span> <span class="d">-${dels}</span></span>
    <span class="diff-toggle">▾</span>`;

  const body = el('div', 'diff-body');
  body.appendChild(buildDiffTable(diff.diff_lines));

  header.addEventListener('click', () => {
    const collapsed = body.classList.toggle('collapsed');
    header.querySelector('.diff-toggle').textContent = collapsed ? '▸' : '▾';
  });

  card.appendChild(header);
  card.appendChild(body);
  return card;
}

function buildDiffTable(lines) {
  const table = el('table', 'diff-table');
  let lo = 0, ln = 0;

  lines.forEach(line => {
    if (line.startsWith('@@')) {
      const m = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (m) { lo = parseInt(m[1]) - 1; ln = parseInt(m[2]) - 1; }
      const tr = el('tr', 'lh');
      tr.innerHTML = `<td colspan="3">${esc(line)}</td>`;
      table.appendChild(tr);
      return;
    }
    if (line.startsWith('---') || line.startsWith('+++')) return;

    const code = esc(line.slice(1));
    let tr, num;

    if (line.startsWith('+')) {
      ln++;
      tr  = el('tr', 'la');
      num = ln;
    } else if (line.startsWith('-')) {
      lo++;
      tr  = el('tr', 'ld');
      num = lo;
    } else {
      lo++; ln++;
      tr  = el('tr', 'lc');
      num = ln;
    }
    tr.innerHTML = `<td class="ln">${num}</td><td class="lt"></td><td>${code}</td>`;
    table.appendChild(tr);
  });
  return table;
}

// ── Renderizado de resultados ──────────────────────────────────────
function renderResults(results) {
  const body = document.getElementById('resultBody');
  body.innerHTML = '';
  if (!results.length) { body.innerHTML = '<span style="color:var(--muted)">Sin detalles disponibles.</span>'; return; }

  results.forEach(r => {
    const div = el('div');
    let html = `<span class="rl">Servicio:</span><strong>${esc(r.service)}</strong> — ${esc(r.status)}`;
    if (r.branch) html += `<br><span class="rl">Rama:</span><code class="rf">${esc(r.branch)}</code>`;
    if (r.pr_info?.url) html += `<br><span class="rl">Pull Request:</span><a class="link" href="${esc(r.pr_info.url)}" target="_blank">${esc(r.pr_info.url)}</a>`;
    if (r.changed_files?.length) {
      html += `<br><span class="rl">Archivos:</span><div style="margin-top:4px">` +
        r.changed_files.map(f => `<div style="color:var(--add-fg);font-family:var(--mono);font-size:12px">✓ ${esc(f)}</div>`).join('') + '</div>';
    }
    div.innerHTML = html;
    body.appendChild(div);
  });
}

// ── Helpers de UI ──────────────────────────────────────────────────
function show(id)   { document.getElementById(id)?.classList.remove('hidden'); }
function hide(id)   { document.getElementById(id)?.classList.add('hidden'); }
function showOnly(id, hideEmpty = true) {
  if (hideEmpty) hide('emptyState');
  show(id);
}
function setText(id, text) { const e = document.getElementById(id); if (e) e.textContent = text; }
function clearLog(id)      { const e = document.getElementById(id); if (e) e.innerHTML = ''; }
function el(tag, cls) { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function logEntry(containerId, text, type = 'info') {
  const c   = document.getElementById(containerId);
  if (!c) return;
  const div = el('div', `log-entry ${type}`);
  div.innerHTML = `<span class="ei"></span><span>${esc(text)}</span>`;
  c.appendChild(div);
  c.scrollTop = c.scrollHeight;
}

function spinner(id, active) {
  const s = document.getElementById(id);
  if (!s) return;
  if (active) s.classList.remove('done'); else s.classList.add('done');
}

function setBtnLoading(loading) {
  const btn  = document.getElementById('analyzeBtn');
  const icon = document.getElementById('analyzeBtnIcon');
  const txt  = document.getElementById('analyzeBtnText');
  btn.disabled   = loading;
  icon.textContent = loading ? '⏳' : '🔍';
  txt.textContent  = loading ? 'Procesando...' : 'Analizar y planificar';
}
