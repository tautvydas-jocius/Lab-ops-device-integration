// Lab-Ops Device Integration - Live QC Telemetry Dashboard
(function () {
  let state = {
    devices: [],
    stats: {},
    recent_readings: [],
    schemas: [],
    filterDevice: 'all',
    filterStableOnly: false,
    filterSearch: '',
    pollIntervalMs: 1500,
    timer: null,
    isPaused: false,
    lastSeenReadingIds: new Set(),
  };

  function init(initialData) {
    if (initialData) {
      applyData(initialData, true);
    }
    setupEventListeners();
    startPolling();
  }

  function applyData(data, isInitial = false) {
    if (!data) return;
    state.stats = data.stats || {};
    state.devices = data.devices || [];
    state.schemas = data.schemas || [];
    
    // Track new readings
    const incomingReadings = data.recent_readings || [];
    const newItems = [];
    incomingReadings.forEach(r => {
      if (!state.lastSeenReadingIds.has(r.id)) {
        state.lastSeenReadingIds.add(r.id);
        if (!isInitial) newItems.push(r);
      }
    });

    state.recent_readings = incomingReadings;

    renderKPIs();
    renderDeviceCards();
    renderAuditTable(newItems);
    updateLastUpdatedTime();
  }

  function renderKPIs() {
    const s = state.stats;
    setText('kpi-total-devices', s.total_devices ?? '--');
    setText('kpi-online-devices', `${s.online_devices ?? 0} / ${s.total_devices ?? 0}`);
    setText('kpi-total-readings', (s.total_readings ?? 0).toLocaleString());
    setText('kpi-stable-pct', `${s.stable_percentage ?? 0}%`);
    setText('kpi-schemas-count', s.schemas_count ?? '--');
    
    // Header status
    const statusPill = document.getElementById('header-mqtt-info');
    if (statusPill && s.mqtt_host) {
      statusPill.textContent = `${s.mqtt_host}:${s.mqtt_port} • ${s.mqtt_prefix}/+`;
    }
  }

  function renderDeviceCards() {
    const grid = document.getElementById('devices-grid');
    if (!grid) return;

    grid.innerHTML = '';
    state.devices.forEach(d => {
      const card = createDeviceCard(d);
      grid.appendChild(card);
    });
  }

  function createDeviceCard(d) {
    const card = document.createElement('div');
    card.className = 'device-card';
    card.id = `device-card-${d.device_id}`;

    const isOnline = d.online;
    const latest = d.latest_reading;
    const valText = latest && latest.value !== null ? parseFloat(latest.value).toFixed(4) : '--.----';
    const unitText = latest && latest.unit ? latest.unit : 'g';
    
    let stabilityClass = 'none';
    let stabilityLabel = 'NO DATA';
    let valClass = '';

    if (latest) {
      if (!latest.ok) {
        stabilityClass = 'error';
        stabilityLabel = 'PARSE ERR';
        valClass = 'error';
      } else if (latest.stable === true) {
        stabilityClass = 'stable';
        stabilityLabel = 'STABLE';
        valClass = 'stable';
      } else if (latest.stable === false) {
        stabilityClass = 'dynamic';
        stabilityLabel = 'DYNAMIC';
        valClass = 'dynamic';
      }
    }

    // Sparkline from recent_values
    const values = d.recent_values || [];
    let sparklineHtml = '';
    if (values.length > 0) {
      const min = Math.min(...values);
      const max = Math.max(...values);
      const range = max - min || 1;
      const bars = values.map((v, i) => {
        const heightPct = Math.max(15, Math.min(100, Math.round(((v - min) / range) * 80 + 20)));
        const barStable = (i === values.length - 1 && latest?.stable) ? 'stable' : 'dynamic';
        return `<div class="sparkline-bar ${barStable}" style="height: ${heightPct}%;" title="${v} ${unitText}"></div>`;
      }).join('');
      sparklineHtml = `<div class="sparkline-container">${bars}</div>`;
    } else {
      sparklineHtml = `<div class="sparkline-container"><div class="sparkline-bar" style="height: 20%;"></div></div>`;
    }

    const rawLine = latest ? latest.raw_line : '(no data received)';

    card.innerHTML = `
      <div class="card-top">
        <div>
          <span class="device-id-badge">${escapeHtml(d.device_id)}</span>
          <h3 class="device-name">${escapeHtml(d.name)}</h3>
          <div class="device-hardware-info">
            <span>${escapeHtml(d.manufacturer || '')} ${escapeHtml(d.model || '')}</span>
            ${d.location ? `<span>• ${escapeHtml(d.location)}</span>` : ''}
          </div>
        </div>
        <span class="status-badge ${isOnline ? 'online' : 'offline'}">
          <span class="dot"></span>
          ${isOnline ? 'Online' : 'Offline'}
        </span>
      </div>

      <div class="display-box">
        <div class="display-main-row">
          <div class="display-weight-wrap">
            <span class="display-weight ${valClass}">${valText}</span>
            <span class="display-unit">${unitText}</span>
          </div>
          <span class="stability-pill ${stabilityClass}">
            ${stabilityClass === 'stable' ? '●' : (stabilityClass === 'dynamic' ? '▲' : '✕')} ${stabilityLabel}
          </span>
        </div>
        ${sparklineHtml}
      </div>

      <div class="specs-list">
        <div class="spec-item">
          <span class="spec-label">Parser Schema</span>
          <span class="spec-val schema-badge">${escapeHtml(d.parser.name)} v${d.parser.version}</span>
        </div>
        <div class="spec-item">
          <span class="spec-label">Config Version</span>
          <span class="spec-val">v${d.config_version} (applied: v${d.applied_config_version ?? '?'})</span>
        </div>
        <div class="spec-item">
          <span class="spec-label">Serial Link</span>
          <span class="spec-val">${d.baud} baud • ${d.poll_interval_ms}ms poll</span>
        </div>
      </div>

      <div class="raw-line-snippet" title="Latest RS232 Raw Line">
        <span style="overflow: hidden; text-overflow: ellipsis;">${escapeHtml(rawLine)}</span>
        <button class="copy-btn" title="Copy raw line" onclick="copyText('${escapeAttr(rawLine)}')">
          <svg viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>
        </button>
      </div>

      <div class="card-actions">
        <button class="btn-card" onclick="filterByDevice('${escapeAttr(d.device_id)}')">
          <svg viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"></path></svg>
          Filter Stream
        </button>
        <button class="btn-card" onclick="showDeviceModal('${escapeAttr(d.device_id)}')">
          <svg viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
          Hardware Config
        </button>
      </div>
    `;

    return card;
  }

  function renderAuditTable(newItems = []) {
    const tbody = document.getElementById('audit-table-body');
    if (!tbody) return;

    let items = state.recent_readings;

    // Apply device filter
    if (state.filterDevice !== 'all') {
      items = items.filter(r => r.device_id === state.filterDevice);
    }

    // Apply stable filter
    if (state.filterStableOnly) {
      items = items.filter(r => r.stable === true);
    }

    // Apply search filter
    if (state.filterSearch) {
      const q = state.filterSearch.toLowerCase();
      items = items.filter(r => 
        (r.raw_line && r.raw_line.toLowerCase().includes(q)) ||
        (r.device_id && r.device_id.toLowerCase().includes(q)) ||
        (r.value && r.value.includes(q)) ||
        (r.parser && r.parser.toLowerCase().includes(q))
      );
    }

    const countBadge = document.getElementById('stream-count-badge');
    if (countBadge) {
      countBadge.textContent = `${items.length} records`;
    }

    tbody.innerHTML = '';

    if (items.length === 0) {
      tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding: 2.5rem; color: var(--text-muted);">No readings match current filter criteria.</td></tr>`;
      return;
    }

    const newIds = new Set(newItems.map(n => n.id));

    items.forEach(r => {
      const tr = document.createElement('tr');
      if (newIds.has(r.id)) {
        tr.className = 'new-entry';
      }

      const formattedVal = r.value !== null ? parseFloat(r.value).toFixed(4) : '--';
      const valClass = r.stable === true ? 'stable' : (r.stable === false ? 'dynamic' : '');

      let stabilityHtml = '';
      if (!r.ok) {
        stabilityHtml = '<span class="stability-pill error" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;">ERROR</span>';
      } else if (r.stable === true) {
        stabilityHtml = '<span class="stability-pill stable" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;">● STABLE</span>';
      } else if (r.stable === false) {
        stabilityHtml = '<span class="stability-pill dynamic" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;">▲ DYNAMIC</span>';
      } else {
        stabilityHtml = '<span class="stability-pill none" style="padding: 0.2rem 0.5rem; font-size: 0.7rem;">--</span>';
      }

      tr.innerHTML = `
        <td class="table-time">${formatDate(r.received_at)}</td>
        <td>
          <strong style="color: var(--text-bright);">${escapeHtml(r.device_id)}</strong>
          <span style="color: var(--text-muted); font-size: 0.75rem;"> ${escapeHtml(r.device_name || '')}</span>
        </td>
        <td class="table-val ${valClass}">${formattedVal} <small style="font-weight: normal; color: var(--text-muted);">${escapeHtml(r.unit || '')}</small></td>
        <td>${stabilityHtml}</td>
        <td><span class="schema-badge">${escapeHtml(r.parser || 'None')}</span></td>
        <td style="font-family: var(--font-mono); color: var(--text-muted);">#${r.seq ?? '--'}</td>
        <td><span class="table-raw" title="${escapeAttr(r.raw_line || '')}">${escapeHtml(r.raw_line || '')}</span></td>
        <td>
          ${r.ok 
            ? '<span style="color: var(--color-stable); font-size: 0.75rem; font-weight:600;">✓ VALID</span>' 
            : `<span style="color: var(--color-error); font-size: 0.75rem; font-weight:600;" title="${escapeAttr(r.error || '')}">✕ INVALID</span>`}
        </td>
      `;

      tbody.appendChild(tr);
    });
  }

  function fetchLatestData() {
    if (state.isPaused) return;

    fetch('/api/overview/')
      .then(res => {
        if (!res.ok) throw new Error(`HTTP error ${res.status}`);
        return res.json();
      })
      .then(data => {
        applyData(data, false);
      })
      .catch(err => {
        console.warn('Dashboard fetch error:', err);
      });
  }

  function startPolling() {
    stopPolling();
    if (state.pollIntervalMs > 0 && !state.isPaused) {
      state.timer = setInterval(fetchLatestData, state.pollIntervalMs);
    }
  }

  function stopPolling() {
    if (state.timer) {
      clearInterval(state.timer);
      state.timer = null;
    }
  }

  function setupEventListeners() {
    // Refresh interval select
    const intervalSelect = document.getElementById('poll-interval-select');
    if (intervalSelect) {
      intervalSelect.addEventListener('change', (e) => {
        const val = parseInt(e.target.value, 10);
        if (val === 0) {
          state.isPaused = true;
          stopPolling();
          setRefreshIndicator('Paused');
        } else {
          state.isPaused = false;
          state.pollIntervalMs = val;
          startPolling();
          setRefreshIndicator(`Live (${val / 1000}s)`);
        }
      });
    }

    // Manual refresh button
    const manualBtn = document.getElementById('btn-refresh-manual');
    if (manualBtn) {
      manualBtn.addEventListener('click', () => {
        manualBtn.classList.add('rotating');
        fetchLatestData();
        setTimeout(() => manualBtn.classList.remove('rotating'), 600);
      });
    }

    // Stable only filter
    const stableCheck = document.getElementById('filter-stable-only');
    if (stableCheck) {
      stableCheck.addEventListener('change', (e) => {
        state.filterStableOnly = e.target.checked;
        renderAuditTable();
      });
    }

    // Search filter input
    const searchInput = document.getElementById('stream-search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (e) => {
        state.filterSearch = e.target.value;
        renderAuditTable();
      });
    }
  }

  window.filterByDevice = function (deviceId) {
    state.filterDevice = deviceId;
    
    // Update active filter pill
    document.querySelectorAll('.filter-pill-btn').forEach(btn => {
      if (btn.dataset.device === deviceId) {
        btn.classList.add('active');
      } else {
        btn.classList.remove('active');
      }
    });

    renderAuditTable();

    // Scroll to audit stream
    const section = document.getElementById('audit-stream-section');
    if (section) {
      section.scrollIntoView({ behavior: 'smooth' });
    }
  };

  window.showDeviceModal = function (deviceId) {
    const d = state.devices.find(x => x.device_id === deviceId);
    if (!d) return;

    const modal = document.getElementById('device-modal');
    const content = document.getElementById('modal-device-content');
    if (!modal || !content) return;

    content.innerHTML = `
      <div class="modal-grid">
        <div class="modal-card">
          <h4>RS232 Serial Port Configuration</h4>
          <div class="specs-list" style="margin-top: 0.5rem;">
            <div class="spec-item"><span class="spec-label">Baud Rate:</span><span class="spec-val">${d.baud}</span></div>
            <div class="spec-item"><span class="spec-label">Data Bits:</span><span class="spec-val">${d.data_bits}</span></div>
            <div class="spec-item"><span class="spec-label">Parity:</span><span class="spec-val">${d.parity}</span></div>
            <div class="spec-item"><span class="spec-label">Stop Bits:</span><span class="spec-val">${d.stop_bits}</span></div>
            <div class="spec-item"><span class="spec-label">Terminator:</span><span class="spec-val"><code>${escapeHtml(d.terminator)}</code></span></div>
            <div class="spec-item"><span class="spec-label">Poll Command:</span><span class="spec-val"><code>${escapeHtml(d.poll_command || 'None (Continuous)')}</code></span></div>
            <div class="spec-item"><span class="spec-label">Poll Interval:</span><span class="spec-val">${d.poll_interval_ms} ms</span></div>
          </div>
        </div>

        <div class="modal-card">
          <h4>Traceable Parser Schema</h4>
          <div class="specs-list" style="margin-top: 0.5rem;">
            <div class="spec-item"><span class="spec-label">Schema:</span><span class="spec-val schema-badge">${escapeHtml(d.parser.name)} v${d.parser.version}</span></div>
            <div class="spec-item"><span class="spec-label">Description:</span><span class="spec-val" style="font-size:0.75rem;">${escapeHtml(d.parser.description || 'N/A')}</span></div>
            <div class="spec-item"><span class="spec-label">Stability Rule:</span><span class="spec-val">${escapeHtml(d.parser.stable_rule)} "${escapeHtml(d.parser.stable_values || '')}"</span></div>
            <div class="spec-item"><span class="spec-label">Stability Group:</span><span class="spec-val">${escapeHtml(d.parser.stable_group || 'N/A')}</span></div>
          </div>
        </div>
      </div>

      <div class="modal-card" style="margin-bottom: 1rem;">
        <h4>Schema Regular Expression</h4>
        <div class="modal-code-block">${escapeHtml(d.parser.pattern)}</div>
      </div>

      <div class="modal-card">
        <h4>MQTT Gateway Integration</h4>
        <div class="specs-list" style="margin-top: 0.5rem;">
          <div class="spec-item"><span class="spec-label">Raw Telemetry:</span><span class="spec-val"><code>${state.stats.mqtt_prefix}/${d.device_id}/raw</code></span></div>
          <div class="spec-item"><span class="spec-label">Status (LWT):</span><span class="spec-val"><code>${state.stats.mqtt_prefix}/${d.device_id}/status</code></span></div>
          <div class="spec-item"><span class="spec-label">Remote Config:</span><span class="spec-val"><code>${state.stats.mqtt_prefix}/${d.device_id}/config</code></span></div>
        </div>
      </div>
    `;

    document.getElementById('modal-device-title').textContent = `${d.device_id} — ${d.name}`;
    modal.classList.add('active');
  };

  window.closeModal = function () {
    const modal = document.getElementById('device-modal');
    if (modal) modal.classList.remove('active');
  };

  window.copyText = function (text) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(() => showToast('Copied to clipboard'));
    } else {
      const ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      showToast('Copied to clipboard');
    }
  };

  function showToast(msg) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 2000);
  }

  function formatDate(isoStr) {
    if (!isoStr) return '--';
    try {
      const d = new Date(isoStr);
      return d.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit', fractionalSecondDigits: 3 });
    } catch {
      return isoStr;
    }
  }

  function updateLastUpdatedTime() {
    const el = document.getElementById('last-updated-time');
    if (el) {
      const now = new Date();
      el.textContent = now.toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }
  }

  function setRefreshIndicator(text) {
    const el = document.getElementById('refresh-mode-text');
    if (el) el.textContent = text;
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function escapeAttr(str) {
    if (str === null || str === undefined) return '';
    return String(str).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  window.LabOpsDashboard = { init };
})();
