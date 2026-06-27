// Programa hecho por Duvelis Huiza modificada por Lic. Luis G.
async function apiGet(path) {
  const res = await fetch(path, { cache: 'no-store' });
  const txt = await res.text();
  if (!res.ok) throw new Error(txt);
  return JSON.parse(txt);
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const txt = await res.text();
  if (!res.ok) throw new Error(txt);
  return JSON.parse(txt);
}

function $(sel) {
  return document.querySelector(sel);
}

function fmtVoltage(v) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(1)}V`;
}

function fmtPct(v) {
  if (v == null || Number.isNaN(v)) return '—';
  const n = Math.max(0, Math.min(100, Math.round(Number(v))));
  return `${n}%`;
}

function isTunnelHost(hostname) {
  const h = String(hostname || '').toLowerCase();
  return h.endsWith('.trycloudflare.com') || (!['127.0.0.1', 'localhost'].includes(h) && h.length > 0);
}

function statusBadge(st) {
  if (st?.running) return { text: 'Running', cls: 'ok' };
  if (st?.stop_requested) return { text: 'Stopped', cls: 'warn' };
  if (st?.exit_code === 0) return { text: 'Idle', cls: 'idle' };
  if (st?.exit_code != null) return { text: 'Error', cls: 'warn' };
  return { text: 'Idle', cls: 'idle' };
}

function drawSpark(svg, values) {
  if (!svg) return;
  svg.innerHTML = '';
  if (!Array.isArray(values) || values.length < 2) return;

  const w = 120;
  const h = 28;
  const xs = values.map((_, i) => (i / (values.length - 1)) * (w - 4) + 2);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = Math.max(1e-6, max - min);
  const ys = values.map((v) => {
    const t = (v - min) / span;
    return (h - 4) - t * (h - 8) + 2;
  });

  const pts = xs.map((x, i) => `${x.toFixed(2)},${ys[i].toFixed(2)}`).join(' ');
  const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
  poly.setAttribute('points', pts);
  poly.setAttribute('fill', 'none');
  poly.setAttribute('stroke', 'rgba(255, 107, 0, 0.92)');
  poly.setAttribute('stroke-width', '2');
  poly.setAttribute('stroke-linecap', 'round');
  poly.setAttribute('stroke-linejoin', 'round');
  svg.appendChild(poly);
}

function setActiveNav(page) {
  document.querySelectorAll('[data-nav]').forEach((a) => {
    a.classList.toggle('active', a.getAttribute('data-nav') === page);
  });
}

async function renderTable(el, columns, rows, maxCols = 10) {
  if (!el) return;
  const cols = (columns || []).slice(0, maxCols);
  const head = `<thead><tr>${cols.map((c) => `<th>${escapeHtml(c)}</th>`).join('')}</tr></thead>`;
  const body = `<tbody>${(rows || []).map((r) => {
    const cells = cols.map((_, i) => `<td>${escapeHtml(r?.[i])}</td>`).join('');
    return `<tr>${cells}</tr>`;
  }).join('')}</tbody>`;
  el.innerHTML = head + body;
}

function escapeHtml(v) {
  const s = v == null ? '' : String(v);
  return s
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function createProviderCard(provider, templateId) {
  const tpl = document.getElementById(templateId);
  const frag = tpl.content.cloneNode(true);
  const card = frag.querySelector('.provider-card');
  const nameEl = frag.querySelector('.provider-name');
  const badgeEl = frag.querySelector('[data-role="badge"]');
  const statusDotEl = frag.querySelector('[data-role="status-dot"]');
  const logEl = frag.querySelector('[data-role="log"]');
  const operativityEl = frag.querySelector('[data-role="operativity"]');
  const btnRun = frag.querySelector('[data-action="run"]');
  const btnStop = frag.querySelector('[data-action="stop"]');
  const btnClear = frag.querySelector('[data-action="clear"]');
  const configLink = frag.querySelector('[data-action="config"]');
  const captureStatusEl = frag.querySelector('[data-role="capture-status"]');

  nameEl.textContent = provider.label.toUpperCase();
  if (configLink) configLink.href = `/settings?provider=${encodeURIComponent(provider.key)}`;

  let pos = 0;
  let status = null;
  let wasRunning = false;

  function renderOperativity(metrics) {
    if (!operativityEl) return;
    if (status?.running) {
      operativityEl.textContent = '—';
      return;
    }
    const p = metrics?.providers?.[provider.key];
    if (!p) {
      operativityEl.textContent = '—';
      return;
    }
    operativityEl.textContent = fmtPct(p.operativity_pct);
  }

  async function refreshStatus() {
    status = await apiGet(`/api/status?provider=${encodeURIComponent(provider.key)}`);
    const b = statusBadge(status);
    if (badgeEl) {
      badgeEl.textContent = b.text;
      badgeEl.classList.remove('ok', 'warn', 'idle');
      badgeEl.classList.add(b.cls);
    }
    
    if (statusDotEl) {
      statusDotEl.classList.remove('idle', 'running', 'error');
      if (b.cls === 'ok') statusDotEl.classList.add('running');
      else if (b.cls === 'warn') statusDotEl.classList.add('error');
      else statusDotEl.classList.add('idle');
    }

    if (captureStatusEl) {
      if (status.running) {
        captureStatusEl.innerHTML = `<span style="color:#ff6b00; animation:pulse-orange 1.5s infinite;">● Capturando...</span>`;
      } else if (status.exit_code === 0) {
        captureStatusEl.innerHTML = `<span style="color:#10b981;">● Finalizado / Listo</span>`;
      } else if (status.exit_code != null) {
        captureStatusEl.innerHTML = `<span style="color:#ef4444;">● Error (Cód: ${status.exit_code})</span>`;
      } else {
        captureStatusEl.innerHTML = `<span style="color:#94a3b8;">● Listo</span>`;
      }
    }

    if (wasRunning && !status.running) {
      if (logEl) {
        logEl.textContent = '';
      }
      pos = 0;
    }
    wasRunning = !!status.running;

    if (btnRun) btnRun.disabled = !!status.running;
    if (btnStop) btnStop.disabled = !status.running;
  }

  async function pollLog() {
    if (!status?.running) return;
    const out = await apiGet(`/api/log?provider=${encodeURIComponent(provider.key)}&pos=${pos}`);
    if (out.text) {
      const atBottom = (logEl.scrollTop + logEl.clientHeight) >= (logEl.scrollHeight - 20);
      logEl.textContent += out.text;
      pos = out.pos;
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
    }
  }

  btnRun.addEventListener('click', async () => {
    try {
      pos = 0;
      logEl.textContent = '';
      await apiPost('/api/run', { provider: provider.key });
    } catch (e) {
      logEl.textContent += `\n[WEBUI] Error al iniciar: ${String(e)}\n`;
    } finally {
      refreshStatus().catch(() => { });
    }
  });

  btnStop.addEventListener('click', async () => {
    try {
      await apiPost('/api/stop', { provider: provider.key });
      logEl.textContent += `\n[WEBUI] Stop requested.\n`;
    } catch (e) {
      logEl.textContent += `\n[WEBUI] Error al detener: ${String(e)}\n`;
    } finally {
      refreshStatus().catch(() => { });
    }
  });

  btnClear.addEventListener('click', async () => {
    try {
      await apiPost('/api/clear', { provider: provider.key });
    } catch (e) {
      logEl.textContent += `\n[WEBUI] Error al limpiar: ${String(e)}\n`;
    }
    pos = 0;
    logEl.textContent = '';
  });

  const updateMetrics = (metrics) => {
    renderOperativity(metrics);
  };

  const start = async () => {
    await refreshStatus();
    setInterval(() => refreshStatus().catch(() => { }), 1500);
    setInterval(() => pollLog().catch(() => { }), 750);
  };

  start().catch(() => { });

  return { el: card, updateMetrics };
}

async function pollTopStatus(config) {
  const top = $('#top-status');
  const hmStatus = $('#hm-status');
  const hmDevices = $('#hm-devices');
  const hmVoltage = $('#hm-voltage');
  const hmNetwork = $('#hm-network');
  const footer = $('#sidebar-foot');
  
  if (footer) footer.textContent = isTunnelHost(location.hostname) ? 'Tunnel' : 'Local';

  const netFooter = $('#sidebar-net');
  if (netFooter && config?.local_url) {
    netFooter.innerHTML = `<a href="${config.local_url}" target="_blank" style="color:var(--primary);text-decoration:none;font-weight:600;">LAN: ${config.local_ip || '—'}</a>`;
  }

  const authText = config?.auth_enabled ? 'Basic' : 'Off';
  const tunnelText = isTunnelHost(location.hostname) ? 'Active' : 'Local';

  async function tick() {
    try {
      const stAll = await apiGet('/api/status');
      const anyRunning = Object.values(stAll).some((s) => s?.running);
      const anyError = Object.values(stAll).some((s) => s?.exit_code != null && s?.exit_code !== 0 && !s?.running);
      
      let statusText = 'Idle';
      if (anyRunning) statusText = 'Running';
      else if (anyError) statusText = 'Error';

      const m = await apiGet('/api/metrics');
      const total = m?.total_devices ?? 0;
      const avg = m?.avg_voltage;
      const avgText = avg == null ? '—' : `${Number(avg).toFixed(1)}V`;

      if (top) {
        top.textContent = `STATUS: ${statusText}  |  CLOUDFLARE TUNNEL: ${tunnelText}  |  AUTH: ${authText}  |  TOTAL DEVICES: ${total}  |  AVG. VOLTAGE: ${avgText}`;
      }

      if (hmStatus) {
        let dotCls = 'idle';
        if (anyRunning) dotCls = 'running';
        else if (anyError) dotCls = 'error';
        hmStatus.innerHTML = `<span class="status-dot ${dotCls}"></span> ${statusText}`;
      }
       if (hmDevices) {
         const gw = m?.providers?.growatt?.device_count ?? 0;
         const sm = m?.providers?.shinemonitor?.device_count ?? 0;
         const vl = m?.providers?.values?.device_count ?? 0;
         hmDevices.innerHTML = `${total} <span style="font-size: 0.82rem; color: #94a3b8; font-weight: normal; margin-left: 8px;">(Growatt: ${gw} | Shine: ${sm} | Values: ${vl})</span>`;
       }
       if (hmVoltage) hmVoltage.textContent = avgText;
       if (hmNetwork) hmNetwork.textContent = `${tunnelText} / ${authText}`;

      return m;
    } catch {
      if (top) top.textContent = 'STATUS: Error conectando al backend';
      if (hmStatus) hmStatus.innerHTML = `<span class="status-dot error"></span> Error DB`;
      return null;
    }
  }

  const first = await tick();
  setInterval(() => tick().catch(() => { }), 3000);
  return first;
}

async function initDashboard(config) {
  const root = $('#provider-cards');
  const templateId = 'provider-card-template';
  const cards = [];

  root.innerHTML = '';
  for (const p of config.providers) {
    const c = createProviderCard(p, templateId);
    root.appendChild(c.el);
    cards.push(c);
  }

  function renderStatusGrid(providerKey, payload) {
    const el = $(`#status-grid-${providerKey}`);
    if (!el) return;
    const plants = payload?.plants || [];
    
    const ok = payload?.ok ?? 0;
    const fail = payload?.fail ?? 0;
    const retry = payload?.retry ?? 0;
    const pending = payload?.pending ?? 0;
    const total = payload?.total ?? 0;
    const pct = total > 0 ? Math.round(((ok + fail) / total) * 100) : 0;
    
    let html = `
      <div class="progress-bar-container" style="width:100%; display:flex; flex-direction:column; gap:8px;">
        <div class="progress-bar-status" style="display:flex; justify-content:space-between; font-size:0.8rem; color:#94a3b8; font-weight:600;">
          <span>Progreso: <strong style="color:#fff;">${pct}%</strong> (${ok + fail}/${total})</span>
          <div style="display:flex; gap:10px;">
            <span style="color:#10b981;">● OK: ${ok}</span>
            <span style="color:#ef4444;">● FAIL: ${fail}</span>
            ${retry > 0 ? `<span style="color:#f59e0b;">● RETRY: ${retry}</span>` : ''}
          </div>
        </div>
        <div class="progress-bar-track" style="display:flex; height:18px; background:rgba(15,23,42,0.6); border:1px solid rgba(255,255,255,0.08); border-radius:9px; overflow:hidden; box-shadow:inset 0 2px 4px rgba(0,0,0,0.5);">
    `;
    
    if (total === 0) {
      html += `<div style="width:100%; background:rgba(255,255,255,0.05);"></div>`;
    } else {
      for (const p of plants) {
        const name = (p?.name || '').trim();
        const tooltip = name ? `${name} (${p.status || 'pending'})` : `Dispositivo (${p.status || 'pending'})`;
        let cls = p.status || 'pending';
        let bg = 'rgba(255,255,255,0.05)';
        if (cls === 'ok') bg = '#10b981';
        else if (cls === 'fail') bg = '#ef4444';
        else if (cls === 'retry') bg = '#f59e0b';
        
        let animate = '';
        if (p.status === 'retry') {
          animate = 'animation: pulse-orange 1.2s infinite;';
        }
        
        html += `<div class="progress-segment" title="${escapeHtml(tooltip)}" style="flex:1; height:100%; background:${bg}; border-right:1px solid rgba(0,0,0,0.2); transition:all 0.3s; ${animate}"></div>`;
      }
    }
    
    const isRunning = !!payload?.running;
    let statusText = isRunning ? '⚡ Capturando datos en tiempo real...' : '💤 Inactivo / En espera';
    if (!isRunning && total > 0 && pending === 0) {
      statusText = '✨ Extracción Completada';
    }
    
    html += `
        </div>
        <div class="progress-current-task" style="font-size:0.75rem; color:#64748b; font-style:italic;">
          Status: ${statusText}
        </div>
      </div>
    `;
    
    el.innerHTML = html;
  }

  function renderNetworkTrend(providerKey, payload) {
    const el = $(`#network-trend-${providerKey}`);
    if (!el) return;
    const series = payload?.series || [];
    const totalOk = payload?.total_ok || 0;
    const totalFail = payload?.total_fail || 0;

    // Summary
    const summaryEl = $(`#trend-summary-${providerKey}`);
    if (summaryEl) {
      summaryEl.innerHTML = `
        <div style="display:flex;gap:1rem;margin-bottom:0.5rem;font-size:0.78rem;">
          <span style="color:#4ade80;">● OK: <strong>${totalOk}</strong></span>
          <span style="color:#f87171;">● FAIL: <strong>${totalFail}</strong></span>
          <span style="color:#64748b;">Total: <strong>${totalOk + totalFail}</strong></span>
        </div>
      `;
    }

    if (series.length === 0) {
      el.innerHTML = '<div style="color:#475569;font-size:0.8rem;text-align:center;padding:2rem;">Sin datos de actividad</div>';
      return;
    }

    const maxVal = Math.max(1, ...series.map(s => (s.ok || 0) + (s.fail || 0)));
    const w = 540;
    const h = 180;
    const padL = 35;
    const padR = 10;
    const padT = 15;
    const padB = 35;
    const chartW = w - padL - padR;
    const chartH = h - padT - padB;
    const n = series.length;
    const barW = Math.floor(chartW / n) - 2;
    const gap = 2;

    let svgContent = '';

    // Y-axis grid lines and labels
    const yTicks = 4;
    for (let i = 0; i <= yTicks; i++) {
      const val = Math.round((maxVal / yTicks) * i);
      const y = padT + chartH - (i / yTicks) * chartH;
      svgContent += `<line x1="${padL}" y1="${y}" x2="${w - padR}" y2="${y}" stroke="rgba(255,255,255,0.05)" stroke-width="1"/>`;
      svgContent += `<text x="${padL - 5}" y="${y + 4}" fill="#475569" font-size="9" text-anchor="end">${val}</text>`;
    }

    // Bars and X labels
    series.forEach((s, i) => {
      const ok = s.ok || 0;
      const fail = s.fail || 0;
      const total = ok + fail;
      const x = padL + i * (barW + gap) + gap;
      const barH = total > 0 ? (total / maxVal) * chartH : 0;
      const okH = total > 0 ? (ok / maxVal) * chartH : 0;
      const failH = total > 0 ? (fail / maxVal) * chartH : 0;

      // OK bar (bottom, green)
      if (ok > 0) {
        const y = padT + chartH - okH;
        svgContent += `<rect x="${x}" y="${y}" width="${barW}" height="${okH}" fill="#4ade80" rx="2" opacity="0.85">
          <title>${s.label || ''} — OK: ${ok}, FAIL: ${fail}</title>
        </rect>`;
      }

      // FAIL bar (stacked on top of ok, red)
      if (fail > 0) {
        const y = padT + chartH - okH - failH;
        svgContent += `<rect x="${x}" y="${y}" width="${barW}" height="${failH}" fill="#f87171" rx="2" opacity="0.85">
          <title>${s.label || ''} — OK: ${ok}, FAIL: ${fail}</title>
        </rect>`;
      }

      // Hover overlay (transparent, for tooltip on empty bars too)
      svgContent += `<rect x="${x}" y="${padT}" width="${barW}" height="${chartH}" fill="transparent">
        <title>${s.label || ''} — OK: ${ok}, FAIL: ${fail}</title>
      </rect>`;

      // X label (show every 3rd to avoid crowding)
      if (i % 3 === 0 || i === n - 1) {
        const labelX = x + barW / 2;
        svgContent += `<text x="${labelX}" y="${h - 8}" fill="#64748b" font-size="9" text-anchor="middle">${s.label || ''}</text>`;
      }
    });

    // Axes
    svgContent += `<line x1="${padL}" y1="${padT + chartH}" x2="${w - padR}" y2="${padT + chartH}" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>`;
    svgContent += `<line x1="${padL}" y1="${padT}" x2="${padL}" y2="${padT + chartH}" stroke="rgba(255,255,255,0.12)" stroke-width="1"/>`;

    el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true" style="width:100%;height:100%;">${svgContent}</svg>`;
  }

  async function loadStatusAndTrend() {
    const providers = ['growatt', 'shinemonitor', 'values'];
    for (const pk of providers) {
      let running = null;
      try {
        const grid = await apiGet(`/api/status-grid?provider=${encodeURIComponent(pk)}`);
        renderStatusGrid(pk, grid);
        running = !!grid?.running;
      } catch { }
      // Por requerimiento: la gráfica de 24h debe empezar a actualizarse
      // una vez termina el proceso de ese gestor.
      if (running === false) {
        try {
          const trend = await apiGet(`/api/network-load?provider=${encodeURIComponent(pk)}`);
          renderNetworkTrend(pk, trend);
        } catch { }
      }
    }
  }

  let lastMetrics = await pollTopStatus(config);
  for (const c of cards) c.updateMetrics(lastMetrics);
  setInterval(async () => {
    const m = await apiGet('/api/metrics');
    for (const c of cards) c.updateMetrics(m);
  }, 3000);

  loadStatusAndTrend().catch(() => { });
  setInterval(() => loadStatusAndTrend().catch(() => { }), 2000);
}

async function initProviders(config) {
  const root = $('#provider-cards');
  const cards = [];
  root.innerHTML = '';
  for (const p of config.providers) {
    const c = createProviderCard(p, 'provider-card-template');
    root.appendChild(c.el);
    cards.push(c);
  }
  let lastMetrics = await pollTopStatus(config);
  for (const c of cards) c.updateMetrics(lastMetrics);
  setInterval(async () => {
    const m = await apiGet('/api/metrics');
    for (const c of cards) c.updateMetrics(m);
  }, 3000);
}

async function initLogs(config) {
  await pollTopStatus(config);
  const sel = $('#log-provider');
  const view = $('#log-view');
  const clearBtn = $('#log-clear');
  const posByProvider = Object.fromEntries(config.providers.map((p) => [p.key, 0]));
  const textByProvider = Object.fromEntries(config.providers.map((p) => [p.key, '']));

  sel.innerHTML = config.providers.map((p) => `<option value="${p.key}">${p.label}</option>`).join('');
  let active = sel.value || config.providers[0]?.key;
  sel.value = active;

  const render = () => {
    view.textContent = textByProvider[active] || '';
    view.scrollTop = view.scrollHeight;
  };

  sel.addEventListener('change', () => {
    active = sel.value;
    render();
  });

  clearBtn.addEventListener('click', async () => {
    try {
      await apiPost('/api/clear', { provider: active });
    } catch { }
    posByProvider[active] = 0;
    textByProvider[active] = '';
    render();
  });

  async function tick() {
    for (const p of config.providers) {
      const out = await apiGet(`/api/log?provider=${encodeURIComponent(p.key)}&pos=${posByProvider[p.key] || 0}`);
      if (out.text) {
        textByProvider[p.key] = (textByProvider[p.key] || '') + out.text;
        posByProvider[p.key] = out.pos;
      }
    }
    render();
  }

  await tick();
  setInterval(() => tick().catch(() => { }), 800);
}

async function initSQLite(config) {
  await pollTopStatus(config);

  const dbSel = $('#db-select');
  const tableSel = $('#table-select');
  const orderSel = $('#order-select');
  const rowsTable = $('#rows-table');
  const refreshBtn = $('#refresh-btn');
  const prevBtn = $('#prev-btn');
  const nextBtn = $('#next-btn');
  const pagerInfo = $('#pager-info');

  let offset = 0;
  const limit = 100;

  const files = (await apiGet('/api/sqlite/files')).files || [];
  dbSel.innerHTML = files.map((f) => `<option value="${escapeHtml(f.name)}">${escapeHtml(f.name)}</option>`).join('');
  const db = dbSel.value;

  async function loadTables() {
    const dbName = dbSel.value;
    const out = await apiGet(`/api/sqlite/tables?db=${encodeURIComponent(dbName)}`);
    tableSel.innerHTML = (out.tables || []).map((t) => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
    offset = 0;
    await loadRows();
  }

  function pickDefaultOrder(cols) {
    const pref = ['captured_at', 'inserted_at', 'last_seen_at', 'update_time', 'id'];
    for (const p of pref) if (cols.includes(p)) return p;
    return cols[0] || null;
  }

  async function loadRows() {
    const dbName = dbSel.value;
    const table = tableSel.value;
    if (!dbName || !table) return;

    const currentOrder = orderSel.value || null;
    const out = await apiGet(
      `/api/sqlite/rows?db=${encodeURIComponent(dbName)}&table=${encodeURIComponent(table)}&limit=${limit}&offset=${offset}` +
      (currentOrder ? `&order_by=${encodeURIComponent(currentOrder)}&desc=1` : '')
    );

    const cols = out.columns || [];
    if (!orderSel.options.length) {
      const def = pickDefaultOrder(cols);
      orderSel.innerHTML = cols.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join('');
      if (def) orderSel.value = def;
    }

    await renderTable(rowsTable, cols, out.rows || [], 30);
    if (pagerInfo) pagerInfo.textContent = `Offset ${offset} · Limit ${limit}`;
  }

  dbSel.addEventListener('change', () => {
    orderSel.innerHTML = '';
    loadTables().catch(() => { });
  });
  tableSel.addEventListener('change', () => {
    orderSel.innerHTML = '';
    offset = 0;
    loadRows().catch(() => { });
  });
  orderSel.addEventListener('change', () => {
    offset = 0;
    loadRows().catch(() => { });
  });
  refreshBtn.addEventListener('click', () => loadRows().catch(() => { }));
  prevBtn.addEventListener('click', () => {
    offset = Math.max(0, offset - limit);
    loadRows().catch(() => { });
  });
  nextBtn.addEventListener('click', () => {
    offset += limit;
    loadRows().catch(() => { });
  });

  if (db) {
    await loadTables();
  }
}

async function initSettings(config) {
  await pollTopStatus(config);
  
  const container = document.getElementById('settings-form-container');
  const fab = document.getElementById('fab-save');
  const btnSave = document.getElementById('btn-save-settings');
  
  if (!container) return;
  const envMap = config?.env || {};

  const groups = [
    { title: "🔌 Cuentas de Scrapers", keys: ["SHINE_USER", "SHINE_PASS", "VALUES_USER", "VALUES_PASS", "GROWATT_USER", "GROWATT_PASS"] },
    { title: "🚀 Opciones Values", keys: ["VALUES_TURBO", "VALUES_USE_DEVICE_LIST", "VALUES_LIMIT_MONITORS"] },
    { title: "⏱️ Tiempos de Espera (Timeouts)", keys: ["SHINE_DEFAULT_TIMEOUT_MS", "SHINE_NAV_TIMEOUT_MS", "VALUES_DEFAULT_TIMEOUT_MS", "VALUES_NAV_TIMEOUT_MS"] },
    { title: "💻 Preferencias del Sistema", keys: ["HEADLESS", "WEBUI_HOST", "WEBUI_PORT"] }
  ];

  let html = '<div class="pro-settings-grid">';
  const seenKeys = new Set();
  
  for (const group of groups) {
    let groupHasKeys = false;
    let groupHtml = `<div class="pro-settings-card"><div class="card-title">${group.title}</div><div class="card-body">`;
      
    for (const key of group.keys) {
      if (!(key in envMap)) continue;
      seenKeys.add(key);
      groupHasKeys = true;
      const val = envMap[key]?.value ?? '';
      
      const isBoolean = val.toLowerCase() === 'true' || val.toLowerCase() === 'false' || val === '1' || val === '0';
      if (isBoolean && (key.includes('TURBO') || key.includes('HEADLESS') || key.includes('DEVICE_LIST'))) {
        const checked = (val.toLowerCase() === 'true' || val === '1') ? 'active' : '';
        const rawValue = (val.toLowerCase() === 'true' || val === '1') ? '1' : '0';
        groupHtml += `<div class="setting-row toggle-row"><label class="setting-label">${key}</label><div class="neon-toggle ${checked}" data-key="${key}" data-val="${rawValue}"></div></div>`;
      } else {
        const type = key.includes('PASS') ? 'password' : (key.includes('TIMEOUT') || key.includes('PORT') ? 'number' : 'text');
        let ph = '';
        if (key.includes('USER')) ph = 'Ej: admin / test@correo.com';
        if (key.includes('PASS')) ph = 'Contraseña...';
        if (key.includes('TIMEOUT')) ph = 'Ej: 30000 (ms)';
        if (key.includes('HOST')) ph = 'Ej: 0.0.0.0';
        if (key.includes('PORT')) ph = 'Ej: 8000';
        groupHtml += `<div class="setting-row"><label class="setting-label">${key}</label><input type="${type}" class="pro-input setting-input" data-key="${key}" value="${escapeHtml(val)}" placeholder="${ph}"></div>`;
      }
    }
    groupHtml += `</div></div>`;
    if (groupHasKeys) html += groupHtml;
  }
  
  let otherHtml = `<div class="pro-settings-card"><div class="card-title">📂 Otras Configuraciones</div><div class="card-body">`;
  let hasOthers = false;
  for (const key of Object.keys(envMap)) {
    if (seenKeys.has(key)) continue;
    hasOthers = true;
    const val = envMap[key]?.value ?? '';
    
    // Si la clave es BROWSER, lo volvemos un combo desplegable (select)
    if (key === 'BROWSER') {
      const bChrome = (val === 'chrome') ? 'selected' : '';
      const bEdge = (val === 'edge') ? 'selected' : '';
      const bFirefox = (val === 'firefox') ? 'selected' : '';
      const bAuto = (!bChrome && !bEdge && !bFirefox) ? 'selected' : '';
      
      otherHtml += `<div class="setting-row"><label class="setting-label">${key}</label>
        <select class="pro-input setting-input" data-key="${key}">
          <option value="" ${bAuto}>Automático (Busca Chrome/Edge/Firefox)</option>
          <option value="chrome" ${bChrome}>Google Chrome</option>
          <option value="edge" ${bEdge}>Microsoft Edge</option>
          <option value="firefox" ${bFirefox}>Mozilla Firefox</option>
        </select>
      </div>`;
    } else {
      otherHtml += `<div class="setting-row"><label class="setting-label">${key}</label><input type="text" class="pro-input setting-input" data-key="${key}" value="${escapeHtml(val)}"></div>`;
    }
  }
  otherHtml += `</div></div>`;
  if (hasOthers) html += otherHtml;
  
  html += '</div>';
  container.innerHTML = html;

  container.querySelectorAll('.neon-toggle').forEach(t => {
    t.addEventListener('click', () => {
      t.classList.toggle('active');
      t.dataset.val = t.classList.contains('active') ? '1' : '0';
    });
  });

  if (btnSave) {
    btnSave.addEventListener('click', async () => {
      btnSave.disabled = true;
      btnSave.textContent = `Guardando...`;
      
      const payload = {};
      container.querySelectorAll('.setting-input').forEach(inp => payload[inp.dataset.key] = inp.value);
      container.querySelectorAll('.neon-toggle').forEach(t => {
        const origVal = envMap[t.dataset.key]?.value?.toLowerCase() || '';
        const isTrueStr = origVal === 'true' || origVal === 'false';
        payload[t.dataset.key] = isTrueStr ? (t.dataset.val === '1' ? 'true' : 'false') : t.dataset.val;
      });

      try {
        const resp = await fetch('/api/settings/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (!resp.ok) throw new Error("Server error");
        const json = await resp.json();
        if (!json.ok) throw new Error(json.error || "Server error");
        
        btnSave.textContent = `¡Guardado Exitoso!`;
        setTimeout(() => {
          btnSave.disabled = false;
          btnSave.textContent = `Guardar Configuración`;
        }, 3000);
      } catch (err) {
        btnSave.textContent = `Error al guardar`;
        setTimeout(() => {
          btnSave.disabled = false;
          btnSave.textContent = `Guardar Configuración`;
        }, 3000);
      }
    });
  }
}

async function initTunnel(config) {
  await pollTopStatus(config);
  const urlEl = $('#tunnel-url');
  const tunEl = $('#tunnel-state');
  const authEl = $('#auth-state');
  if (urlEl) urlEl.textContent = location.origin;
  if (tunEl) tunEl.textContent = isTunnelHost(location.hostname) ? 'Active' : 'Local';
  if (authEl) authEl.textContent = config?.auth_enabled ? 'Basic' : 'Off';
}

async function initReports(config) {
  await pollTopStatus(config);

  const slotSel = $('#report-slot'); // Hidden select
  const cards = document.querySelectorAll('.slot-card');
  const exportBtn = $('#report-export-pro');
  const statusEl = $('#report-status-text');
  const historyEl = $('#report-history');

  // Handle slot card selection
  cards.forEach(card => {
    card.addEventListener('click', () => {
      cards.forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      if (slotSel) slotSel.value = card.dataset.slot;
      setStatus(`Listo para extraer: ${card.querySelector('.slot-name').textContent}`, false);
    });
  });

  function setStatus(text, isError = false) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.color = isError ? '#ef4444' : '#94a3b8';
  }

  async function refresh() {
    // Basic status poll won't interfere heavily with UI 
  }

  function fmtBytes(n) {
    const v = Number(n || 0);
    if (!Number.isFinite(v) || v <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB'];
    let x = v;
    let i = 0;
    while (x >= 1024 && i < units.length - 1) {
      x /= 1024;
      i += 1;
    }
    return `${i === 0 ? String(Math.round(x)) : x.toFixed(1)} ${units[i]}`;
  }

  function fmtWhen(ts) {
    const d = new Date((Number(ts || 0) || 0) * 1000);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString([], { dateStyle: 'short', timeStyle: 'short' });
  }

  async function loadHistory() {
    if (!historyEl) return;
    try {
      const out = await apiGet('/api/report/history');
      const files = out?.files || [];

      const groupByDate = {};
      for (const f of files) {
        const d = new Date((Number(f?.mtime || 0) || 0) * 1000);
        if (Number.isNaN(d.getTime())) continue;
        const dateKey = `${d.getDate().toString().padStart(2, '0')}/${(d.getMonth() + 1).toString().padStart(2, '0')}/${d.getFullYear()}`;
        if (!groupByDate[dateKey]) groupByDate[dateKey] = [];
        groupByDate[dateKey].push(f);
      }

      const sortedDates = Object.keys(groupByDate).sort((a, b) => {
        const toNum = (s) => parseInt(s.split('/').reverse().join(''));
        return toNum(b) - toNum(a);
      });

      const dateSelect = document.getElementById('history-date-select');
      if (dateSelect) {
        const currentVal = dateSelect.value;
        dateSelect.innerHTML = '';
        for (const dateKey of sortedDates) {
          const opt = document.createElement('option');
          opt.value = dateKey;
          opt.textContent = dateKey;
          dateSelect.appendChild(opt);
        }
        if (currentVal && sortedDates.includes(currentVal)) {
          dateSelect.value = currentVal;
        } else if (sortedDates.length > 0) {
          dateSelect.value = sortedDates[0];
        }
        dateSelect.onchange = () => renderHistoryForDate(dateSelect.value);
        renderHistoryForDate(dateSelect.value);
      }

      function renderHistoryForDate(dateKey) {
        historyEl.innerHTML = '';
        const filesForDate = groupByDate[dateKey] || [];
        if (filesForDate.length === 0) {
          historyEl.innerHTML = '<div class="muted" style="padding: 24px; text-align: center;">No hay reportes para esta fecha.</div>';
          return;
        }

        for (const f of filesForDate) {
          const name = String(f?.name || '').trim();
          if (!name) continue;

          const row = document.createElement('div');
          row.className = 'history-item-pro';

          row.innerHTML = `
            <div class="hi-icon">📊</div>
            <div class="hi-details">
              <div class="hi-name">${escapeHtml(name)}</div>
              <div class="hi-meta">
                <span>🕒 ${escapeHtml(fmtWhen(f?.mtime))}</span>
                <span>📦 ${escapeHtml(fmtBytes(f?.size))}</span>
              </div>
            </div>
            <div class="hi-actions">
              <button class="btn-pro" title="Descargar" data-action="download">📥 Descargar</button>
              <button class="btn-pro danger" title="Eliminar" data-action="delete">🗑️ Eliminar</button>
            </div>
          `;

          const dl = row.querySelector('[data-action="download"]');
          const del = row.querySelector('[data-action="delete"]');

          dl?.addEventListener('click', () => {
            location.href = `/api/report/download?file=${encodeURIComponent(name)}`;
          });

          del?.addEventListener('click', async () => {
            if (!confirm(`¿Estás seguro de eliminar permanentemente "${name}"?`)) return;
            try {
              const oldHtml = del.innerHTML;
              del.innerHTML = '⏳...'; del.disabled = true;
              await apiPost('/api/report/delete', { file: name });
              await loadHistory();
            } catch (err) {
              alert(`Error al eliminar: ${String(err)}`);
              del.innerHTML = '🗑️ Eliminar'; del.disabled = false;
            }
          });

          historyEl.appendChild(row);
        }
      }
    } catch (e) {
      historyEl.innerHTML = `<div class="muted" style="padding:24px; color:#ef4444;">Error cargando historial: ${escapeHtml(String(e))}</div>`;
    }
  }

  exportBtn?.addEventListener('click', async () => {
    const slot = slotSel?.value || 'manana';
    setStatus('Procesando extracción y guardando archivo Excel...', false);

    if (exportBtn) {
      exportBtn.disabled = true;
      exportBtn.classList.add('loading');
    }

    const rid = `${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
    location.href = `/api/report/export?slot=${encodeURIComponent(slot)}&rid=${encodeURIComponent(rid)}`;

    setTimeout(() => {
      if (exportBtn) {
        exportBtn.disabled = false;
        exportBtn.classList.remove('loading');
      }
      setStatus('Extracción finalizada o en curso.', false);
      loadHistory().catch(() => { });
    }, 3000);
  });

  const deleteAllBtn = $('#btn-delete-all-reports');
  deleteAllBtn?.addEventListener('click', async () => {
    if (!confirm('¿Estás seguro de que deseas eliminar permanentemente TODOS los reportes generados en el historial?')) return;
    try {
      deleteAllBtn.disabled = true;
      const oldText = deleteAllBtn.innerHTML;
      deleteAllBtn.innerHTML = '⏳...';
      await apiPost('/api/report/delete-all');
      await loadHistory();
      deleteAllBtn.innerHTML = oldText;
      deleteAllBtn.disabled = false;
    } catch (err) {
      alert(`Error al eliminar todos los reportes: ${String(err)}`);
      deleteAllBtn.innerHTML = '🗑️ Eliminar Todos';
      deleteAllBtn.disabled = false;
    }
  });

  await loadHistory();
  setInterval(() => loadHistory().catch(() => { }), 15000);
}

async function main() {
  const page = document.body?.dataset?.page || 'dashboard';
  setActiveNav(page);
  const config = await apiGet('/api/config');

  if (page === 'dashboard') return initDashboard(config);
  if (page === 'providers') return initProviders(config);
  if (page === 'sqlite') return initSQLite(config);
  if (page === 'logs') return initLogs(config);
  if (page === 'settings') return initSettings(config);
  if (page === 'tunnel') return initTunnel(config);
  if (page === 'reports') return initReports(config);
}

main().catch((e) => {
  document.body.innerHTML = `<pre style="padding:16px; color:#fff">Error cargando UI: ${escapeHtml(String(e))}</pre>`;
});
