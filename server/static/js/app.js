(function () {
  const state = {
    ws: null,
    password: localStorage.getItem('ra_admin_password') || 'admin123',
    agents: new Map(),
    knownDevices: [],
    offlineCount: 0,
    currentFilter: 'all',
    searchQuery: '',
    currentView: 'wall',
    selectedAgentId: null,
    logsCache: [],
  };

  // Referencias DOM Generales
  const authModal = document.getElementById('auth-modal');
  const authForm = document.getElementById('auth-form');
  const passInput = document.getElementById('admin-pass-input');
  const authError = document.getElementById('auth-error');

  const aliasModal = document.getElementById('alias-modal');
  const aliasForm = document.getElementById('alias-form');
  const aliasAgentIdInput = document.getElementById('alias-agent-id');
  const aliasTextInput = document.getElementById('alias-input');
  const aliasCancelBtn = document.getElementById('alias-cancel-btn');

  const wsStatusPill = document.getElementById('ws-status-pill');
  const wsStatusText = document.getElementById('ws-status-text');

  // Pestañas y Vistas
  const tabWall = document.getElementById('tab-wall');
  const tabViewer = document.getElementById('tab-viewer');
  const tabActivity = document.getElementById('tab-activity');
  const tabLogs = document.getElementById('tab-logs');
  const tabViewerLabel = document.getElementById('tab-viewer-label');

  const viewWall = document.getElementById('view-wall');
  const viewViewer = document.getElementById('view-viewer');
  const viewActivity = document.getElementById('view-activity');
  const viewLogs = document.getElementById('view-logs');

  // Vista 1: Muro
  const emptyState = document.getElementById('empty-state');
  const agentsGrid = document.getElementById('agents-grid');
  const countTotal = document.getElementById('count-total');
  const countActive = document.getElementById('count-active');
  const countIdle = document.getElementById('count-idle');
  const countAway = document.getElementById('count-away');
  const countOffline = document.getElementById('count-offline');
  const searchInput = document.getElementById('search-input');
  const refreshThumbsBtn = document.getElementById('refresh-thumbs-btn');

  // Vista 2: Visor
  const backToWallBtn = document.getElementById('back-to-wall-btn');
  const viewerStatusDot = document.getElementById('viewer-status-dot');
  const viewerDeviceAlias = document.getElementById('viewer-device-alias');
  const viewerEditAliasBtn = document.getElementById('viewer-edit-alias-btn');
  const viewerActiveWindow = document.getElementById('viewer-active-window');
  const monitorSelect = document.getElementById('monitor-select');
  const qualitySelect = document.getElementById('quality-select');
  const toggleControlBtn = document.getElementById('toggle-control-btn');
  const pttBtn = document.getElementById('ptt-btn');
  const openClipboardBtn = document.getElementById('open-clipboard-btn');
  const openFileBtn = document.getElementById('open-file-btn');
  const toggleChatSidebarBtn = document.getElementById('toggle-chat-sidebar-btn');
  const chatUnreadBadge = document.getElementById('chat-unread-badge');
  const viewerLogsBtn = document.getElementById('viewer-logs-btn');
  const fullscreenBtn = document.getElementById('fullscreen-btn');
  const viewerLoading = document.getElementById('viewer-loading');

  // Fase 3: Panel Lateral de Chat
  const chatSidebar = document.getElementById('chat-sidebar');
  const closeChatSidebarBtn = document.getElementById('close-chat-sidebar-btn');
  const chatMessagesList = document.getElementById('chat-messages-list');
  const chatSendForm = document.getElementById('chat-send-form');
  const chatInput = document.getElementById('chat-input');

  // Fase 3: Modal de Portapapeles
  const clipboardModal = document.getElementById('clipboard-modal');
  const readRemoteClipBtn = document.getElementById('read-remote-clip-btn');
  const copyLocalClipBtn = document.getElementById('copy-local-clip-btn');
  const remoteClipOutput = document.getElementById('remote-clip-output');
  const sendClipForm = document.getElementById('send-clip-form');
  const sendClipInput = document.getElementById('send-clip-input');
  const clipAutopasteCheck = document.getElementById('clip-autopaste-check');
  const clipStatusMsg = document.getElementById('clip-status-msg');
  const closeClipboardBtn = document.getElementById('close-clipboard-btn');

  // Fase 3: Modal de Envío de Archivos
  const fileModal = document.getElementById('file-modal');
  const fileUploadForm = document.getElementById('file-upload-form');
  const fileDropzone = document.getElementById('file-dropzone');
  const fileInput = document.getElementById('file-input');
  const fileDropzoneLabel = document.getElementById('file-dropzone-label');
  const fileDestinationSelect = document.getElementById('file-destination-select');
  const fileAutoopenCheck = document.getElementById('file-autoopen-check');
  const fileStatusMsg = document.getElementById('file-status-msg');
  const closeFileBtn = document.getElementById('close-file-btn');
  const submitFileBtn = document.getElementById('submit-file-btn');

  let selectedUploadFile = null;
  let chatUnreadCount = 0;

  // Vista 3: Métricas
  const metricsDateInput = document.getElementById('metrics-date-input');
  const refreshMetricsBtn = document.getElementById('refresh-metrics-btn');
  const metricsTbody = document.getElementById('metrics-tbody');

  // Vista 4: Logs
  const logDeviceFilter = document.getElementById('log-device-filter');
  const logCategoryFilter = document.getElementById('log-category-filter');
  const logSearchInput = document.getElementById('log-search-input');
  const exportCsvBtn = document.getElementById('export-csv-btn');
  const logsTbody = document.getElementById('logs-tbody');

  // Inicializar fecha de métricas en hoy
  const todayIso = new Date().toISOString().slice(0, 10);
  if (metricsDateInput) {
    metricsDateInput.value = todayIso;
  }

  const canvasEl = document.getElementById('remote-canvas');
  const stageEl = document.getElementById('canvas-stage');

  const viewer = new RemoteCanvasViewer(canvasEl, stageEl, (payload) => {
    sendWsJson(payload);
  });

  const pttController =
    typeof PushToTalkController !== 'undefined'
      ? new PushToTalkController(
          pttBtn,
          (wavB64) => {
            if (state.selectedAgentId) {
              sendWsJson({
                type: 'audio_chunk',
                agent_id: state.selectedAgentId,
                wav_b64: wavB64,
              });
            }
          },
          () => {
            if (state.selectedAgentId) {
              sendWsJson({
                type: 'audit_ptt_start',
                agent_id: state.selectedAgentId,
              });
            }
          }
        )
      : null;

  function setWsBadge(mode, text) {
    wsStatusPill.className = `status-pill status-${mode}`;
    wsStatusText.textContent = text;
  }

  function sendWsJson(obj) {
    if (state.ws && state.ws.readyState === WebSocket.OPEN) {
      state.ws.send(JSON.stringify(obj));
    }
  }

  function formatIdleTime(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    if (s < 15) return 'Activo ahora';
    if (s < 60) return `Hace ${s} seg`;
    const mins = Math.floor(s / 60);
    if (mins < 60) return `Inactivo: ${mins} min`;
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `Inactivo: ${hrs}h ${remMins}m`;
  }

  function formatAccumulatedTime(seconds) {
    const s = Math.max(0, Math.round(Number(seconds) || 0));
    if (s < 60) return `${s}s`;
    const mins = Math.floor(s / 60);
    const secs = s % 60;
    if (mins < 60) return secs > 0 ? `${mins}m ${secs}s` : `${mins}m`;
    const hrs = Math.floor(mins / 60);
    const remMins = mins % 60;
    return `${hrs}h ${remMins}m`;
  }

  function getStatusMeta(status, idleSeconds) {
    if (status === 'active') {
      return {
        dotClass: 'dot-emerald',
        badgeClass: 'badge-active',
        label: idleSeconds < 15 ? 'Activo ahora' : `Activo (${idleSeconds}s)`,
      };
    }
    if (status === 'idle') {
      const m = Math.max(1, Math.floor(idleSeconds / 60));
      return {
        dotClass: 'dot-amber',
        badgeClass: 'badge-idle',
        label: `Inactivo ${m} min`,
      };
    }
    if (status === 'offline') {
      return {
        dotClass: 'dot-slate',
        badgeClass: 'cat-NETWORK',
        label: 'Desconectado',
      };
    }
    const m = Math.max(1, Math.floor(idleSeconds / 60));
    return {
      dotClass: 'dot-rose',
      badgeClass: 'badge-away',
      label: `Ausente ${m} min`,
    };
  }

  function escapeHtml(str) {
    return String(str || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function updateCountersAndGrid() {
    const all = Array.from(state.agents.values());
    let activeCount = 0;
    let idleCount = 0;
    let awayCount = 0;

    all.forEach((ag) => {
      if (ag.activity_status === 'active') activeCount += 1;
      else if (ag.activity_status === 'idle') idleCount += 1;
      else awayCount += 1;
    });

    countTotal.textContent = String(all.length);
    countActive.textContent = String(activeCount);
    countIdle.textContent = String(idleCount);
    countAway.textContent = String(awayCount);
    if (countOffline) {
      countOffline.textContent = String(state.offlineCount || 0);
    }

    if (all.length === 0) {
      emptyState.classList.remove('hidden');
      agentsGrid.classList.add('hidden');
      return;
    }

    emptyState.classList.add('hidden');
    agentsGrid.classList.remove('hidden');

    const q = state.searchQuery.trim().toLowerCase();
    const filtered = all.filter((ag) => {
      if (state.currentFilter !== 'all' && ag.activity_status !== state.currentFilter) {
        return false;
      }
      if (!q) return true;
      const hay = `${ag.alias} ${ag.hostname} ${ag.username} ${ag.local_ip} ${ag.active_window}`.toLowerCase();
      return hay.includes(q);
    });

    agentsGrid.innerHTML = filtered
      .map((ag) => {
        const meta = getStatusMeta(ag.activity_status, ag.idle_seconds);
        const monCount = Math.max(1, (ag.monitors || []).filter((m) => m.id > 0).length);
        const thumbSrc = ag.thumbnail ? `data:image/jpeg;base64,${ag.thumbnail}` : '';
        const cardExtra = ag.activity_status === 'away' ? 'card-away' : '';

        return `
          <article class="agent-card ${cardExtra}" data-agent-id="${escapeHtml(ag.agent_id)}">
            <div>
              <div class="card-header">
                <div class="card-title-group">
                  <span class="indicator-dot ${meta.dotClass}"></span>
                  <div style="min-width:0;">
                    <div class="card-alias-row">
                      <span class="card-alias" title="${escapeHtml(ag.alias)}">${escapeHtml(ag.alias)}</span>
                      <button type="button" class="icon-btn js-edit-alias" data-agent-id="${escapeHtml(ag.agent_id)}" title="Renombrar equipo">
                        <svg class="icon-xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                          <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
                        </svg>
                      </button>
                    </div>
                    <p class="card-submeta">${escapeHtml(ag.hostname)} • ${escapeHtml(ag.username)} • ${monCount} Monitor${monCount > 1 ? 'es' : ''}</p>
                  </div>
                </div>
                <span class="badge-status ${meta.badgeClass}">${escapeHtml(meta.label)}</span>
              </div>

              <div class="card-preview js-open-viewer" data-agent-id="${escapeHtml(ag.agent_id)}">
                ${thumbSrc ? `<img src="${thumbSrc}" alt="Vista previa de ${escapeHtml(ag.alias)}" />` : ''}
                <div class="card-preview-overlay">
                  <div class="window-pill">
                    <svg class="icon-xs muted-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/>
                    </svg>
                    <span>${escapeHtml(ag.active_window || 'Escritorio')}</span>
                  </div>
                  <div class="preview-footer">
                    <span>${escapeHtml(formatIdleTime(ag.idle_seconds))}</span>
                    <span>IP: ${escapeHtml(ag.local_ip || '---')}</span>
                  </div>
                </div>
              </div>
            </div>

            <div class="card-footer">
              <button type="button" class="btn btn-primary js-open-viewer" style="flex:1;" data-agent-id="${escapeHtml(ag.agent_id)}">
                <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>
                  <circle cx="12" cy="12" r="3"/>
                </svg>
                <span>Conectar</span>
              </button>
              <button type="button" class="btn btn-secondary js-open-chat" data-agent-id="${escapeHtml(ag.agent_id)}" title="Abrir visor y enviar mensaje directo">
                <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
                </svg>
                <span>Chat</span>
              </button>
              <button type="button" class="btn btn-secondary js-open-logs" data-agent-id="${escapeHtml(ag.agent_id)}" title="Ver bitácora de logs de este equipo">
                <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
                </svg>
                <span>Logs</span>
              </button>
            </div>
          </article>
        `;
      })
      .join('');
  }

  function populateLogDeviceSelector() {
    const currentVal = logDeviceFilter.value || 'ALL';
    const mapDevs = new Map();
    state.knownDevices.forEach((d) => mapDevs.set(d.agent_id, d.alias));
    state.agents.forEach((a) => mapDevs.set(a.agent_id, a.alias));

    let html = '<option value="ALL">Todos los equipos</option>';
    mapDevs.forEach((alias, id) => {
      const sel = id === currentVal ? 'selected' : '';
      html += `<option value="${escapeHtml(id)}" ${sel}>${escapeHtml(alias)}</option>`;
    });
    logDeviceFilter.innerHTML = html;
  }

  async function loadDailyMetrics() {
    const dateVal = metricsDateInput.value || todayIso;
    try {
      const res = await fetch(`/api/metrics?date=${encodeURIComponent(dateVal)}`);
      const data = await res.json();
      const rows = data.metrics || [];
      if (rows.length === 0) {
        metricsTbody.innerHTML = '<tr><td colspan="7" class="empty-row">No hay registros de actividad para la fecha seleccionada.</td></tr>';
        return;
      }

      metricsTbody.innerHTML = rows
        .map((m) => {
          const meta = getStatusMeta(m.activity_status, m.current_idle_seconds || 0);
          const appsHtml =
            (m.top_apps || []).length > 0
              ? `<div class="app-tags-wrap">${m.top_apps
                  .map(
                    (ap) =>
                      `<span class="app-usage-pill"><span>${escapeHtml(ap.app_name)}</span><span class="app-usage-pct">${ap.percent}%</span></span>`
                  )
                  .join('')}</div>`
              : '<span class="muted-text">Sin datos aún</span>';

          const alertsBadge =
            m.away_alerts_count > 0
              ? `<span class="alert-count-badge">${m.away_alerts_count} alerta${m.away_alerts_count > 1 ? 's' : ''}</span>`
              : '';

          return `
            <tr>
              <td>
                <strong>${escapeHtml(m.alias)}</strong>
                <div class="muted-text">${escapeHtml(m.hostname)} (${escapeHtml(m.username)})</div>
              </td>
              <td>
                <span class="badge-status ${meta.badgeClass}">${escapeHtml(meta.label)}</span>
              </td>
              <td>${escapeHtml(m.first_seen_at || '---')}</td>
              <td><strong class="text-emerald">${escapeHtml(formatAccumulatedTime(m.active_seconds))}</strong></td>
              <td>
                <span>${escapeHtml(formatAccumulatedTime(m.idle_seconds))}</span>
                ${alertsBadge}
              </td>
              <td>${appsHtml}</td>
              <td>
                <div style="display:flex; gap:6px;">
                  ${
                    m.is_online
                      ? `<button type="button" class="btn btn-primary btn-sm js-metric-connect" data-agent-id="${escapeHtml(m.agent_id)}">Ver</button>`
                      : ''
                  }
                  <button type="button" class="btn btn-secondary btn-sm js-metric-logs" data-agent-id="${escapeHtml(m.agent_id)}">Logs</button>
                </div>
              </td>
            </tr>
          `;
        })
        .join('');
    } catch (err) {
      console.error('Error al cargar métricas:', err);
    }
  }

  function renderLogsTable() {
    if (!state.logsCache || state.logsCache.length === 0) {
      logsTbody.innerHTML = '<tr><td colspan="4" class="empty-row">No se encontraron eventos con los filtros seleccionados.</td></tr>';
      return;
    }

    const categoryLabels = {
      IDLE: 'INACTIVIDAD',
      RESUME: 'REGRESO',
      WINDOW: 'VENTANA ACTIVA',
      NETWORK: 'CONEXIÓN',
      AUDIT: 'SOPORTE',
    };

    logsTbody.innerHTML = state.logsCache
      .map((row) => {
        const cat = row.category || 'WINDOW';
        const catText = categoryLabels[cat] || cat;
        return `
          <tr>
            <td class="muted-text">${escapeHtml(row.timestamp)}</td>
            <td>
              <strong>${escapeHtml(row.alias)}</strong>
              <div class="muted-text">${escapeHtml(row.hostname)}</div>
            </td>
            <td><span class="cat-badge cat-${escapeHtml(cat)}">${escapeHtml(catText)}</span></td>
            <td>${escapeHtml(row.message)}</td>
          </tr>
        `;
      })
      .join('');
  }

  async function fetchLogsFromServer() {
    const dev = logDeviceFilter.value || 'ALL';
    const cat = logCategoryFilter.value || 'ALL';
    const q = logSearchInput.value.trim();

    const params = new URLSearchParams({
      agent_id: dev,
      category: cat,
      q,
      limit: '250',
    });

    try {
      const res = await fetch(`/api/logs?${params.toString()}`);
      const data = await res.json();
      if (Array.isArray(data.devices)) {
        state.knownDevices = data.devices;
        populateLogDeviceSelector();
      }
      state.logsCache = data.logs || [];
      renderLogsTable();
    } catch (err) {
      console.error('Error al consultar bitácora de logs:', err);
    }
  }

  function openLogsForAgent(agentId) {
    populateLogDeviceSelector();
    if (agentId) {
      logDeviceFilter.value = agentId;
    }
    switchView('logs');
    fetchLogsFromServer();
  }

  function updateViewerHeader(agent) {
    if (!agent) return;
    const meta = getStatusMeta(agent.activity_status, agent.idle_seconds);
    viewerStatusDot.className = `indicator-dot ${meta.dotClass}`;
    viewerDeviceAlias.textContent = agent.alias;
    viewerActiveWindow.textContent = `Ventana activa: ${agent.active_window || 'Escritorio'}`;
    tabViewerLabel.textContent = `Visor: ${agent.alias}`;
  }

  function populateMonitorsDropdown(agent) {
    if (!agent || !Array.isArray(agent.monitors) || agent.monitors.length === 0) {
      monitorSelect.innerHTML = '<option value="1">Monitor 1</option>';
      return;
    }
    const indiv = agent.monitors.filter((m) => m.id > 0);
    const allCombined = agent.monitors.filter((m) => m.id === 0);
    const ordered = indiv.length > 1 ? [...indiv, ...allCombined] : indiv;

    monitorSelect.innerHTML = ordered
      .map((m) => `<option value="${m.id}" ${m.id === agent.current_monitor ? 'selected' : ''}>${escapeHtml(m.label)}</option>`)
      .join('');
  }

  function formatShortTime(ts) {
    if (!ts) return '';
    const str = String(ts);
    return str.length >= 19 ? str.slice(11, 16) : str;
  }

  function renderChatMessageBubble(msg) {
    const isAdmin = msg.sender === 'admin';
    const bubbleClass = isAdmin ? 'chat-msg chat-msg-admin' : 'chat-msg chat-msg-agent';
    const who = isAdmin ? 'Soporte (Tú)' : msg.sender_name || 'Empleado';
    const hm = formatShortTime(msg.timestamp);
    return `
      <div class="${bubbleClass}">
        <div class="chat-msg-meta">
          <strong>${escapeHtml(who)}</strong>
          <span>${escapeHtml(hm)}</span>
        </div>
        <div class="chat-msg-body">${escapeHtml(msg.message)}</div>
      </div>
    `;
  }

  async function loadChatHistoryForAgent(agentId) {
    if (!chatMessagesList || !agentId) return;
    try {
      const res = await fetch(`/api/chat/${encodeURIComponent(agentId)}?limit=100`);
      const data = await res.json();
      const list = data.messages || [];
      if (list.length === 0) {
        chatMessagesList.innerHTML = `
          <div class="chat-empty-state">
            No hay mensajes en esta sesión. Mientras no envíes un mensaje, el empleado no verá ninguna ventana de chat.
          </div>
        `;
        return;
      }
      chatMessagesList.innerHTML = list.map(renderChatMessageBubble).join('');
      chatMessagesList.scrollTop = chatMessagesList.scrollHeight;
    } catch (err) {
      console.error('Error al cargar historial de chat:', err);
    }
  }

  function appendLiveChatMessage(chatObj) {
    if (!chatMessagesList || !chatObj) return;
    const emptyEl = chatMessagesList.querySelector('.chat-empty-state');
    if (emptyEl) {
      emptyEl.remove();
    }
    chatMessagesList.insertAdjacentHTML('beforeend', renderChatMessageBubble(chatObj));
    chatMessagesList.scrollTop = chatMessagesList.scrollHeight;
  }

  function setChatSidebarOpen(isOpen) {
    if (!chatSidebar || !toggleChatSidebarBtn) return;
    chatSidebar.classList.toggle('hidden', !isOpen);
    toggleChatSidebarBtn.classList.toggle('active-tool', isOpen);
    if (isOpen) {
      chatUnreadCount = 0;
      if (chatUnreadBadge) {
        chatUnreadBadge.textContent = '0';
        chatUnreadBadge.classList.add('hidden');
      }
    }
  }

  function sendDirectChatMessage(text) {
    const clean = String(text || '').trim();
    if (!clean || !state.selectedAgentId) return;
    sendWsJson({
      type: 'chat_send',
      agent_id: state.selectedAgentId,
      text: clean,
    });
  }

  function openViewerForAgent(agentId, options = {}) {
    const agent = state.agents.get(agentId);
    if (!agent) return;

    state.selectedAgentId = agentId;
    tabViewer.disabled = false;
    viewer.setAgent(agentId);

    updateViewerHeader(agent);
    populateMonitorsDropdown(agent);
    loadChatHistoryForAgent(agentId);

    viewerLoading.classList.remove('hidden');
    switchView('viewer');

    if (options.focusChat) {
      setChatSidebarOpen(true);
      setTimeout(() => {
        if (chatInput) chatInput.focus();
      }, 120);
    }

    sendWsJson({
      type: 'subscribe',
      agent_id: agentId,
    });
  }

  function stopStreamIfLeavingViewer(nextView) {
    if (state.currentView === 'viewer' && nextView !== 'viewer' && state.selectedAgentId) {
      if (pttController && pttController.isRecording) {
        pttController.stopRecording();
      }
      sendWsJson({ type: 'unsubscribe' });
      viewer.setControlMode(false);
    }
  }

  function switchView(target) {
    stopStreamIfLeavingViewer(target);
    state.currentView = target;

    const views = {
      wall: viewWall,
      viewer: viewViewer,
      activity: viewActivity,
      logs: viewLogs,
    };
    const tabs = {
      wall: tabWall,
      viewer: tabViewer,
      activity: tabActivity,
      logs: tabLogs,
    };

    Object.keys(views).forEach((k) => {
      views[k].classList.toggle('hidden', k !== target);
      tabs[k].classList.toggle('active', k === target);
    });

    if (target === 'activity') {
      loadDailyMetrics();
    } else if (target === 'logs') {
      fetchLogsFromServer();
    }
  }

  function openAliasEditor(agentId) {
    const ag = state.agents.get(agentId);
    if (!ag) return;
    aliasAgentIdInput.value = agentId;
    aliasTextInput.value = ag.alias;
    aliasModal.classList.remove('hidden');
    aliasTextInput.focus();
    aliasTextInput.select();
  }

  function connectAdminWebSocket() {
    setWsBadge('connecting', 'Conectando...');
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${proto}//${window.location.host}/ws/admin`;

    const ws = new WebSocket(wsUrl);
    ws.binaryType = 'arraybuffer';
    state.ws = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          type: 'auth',
          password: state.password,
        })
      );
    };

    ws.onmessage = (evt) => {
      if (evt.data instanceof ArrayBuffer) {
        viewer.renderBinaryFrame(evt.data);
        return;
      }

      const msg = JSON.parse(evt.data);
      if (msg.type === 'auth_error') {
        setWsBadge('disconnected', 'Clave requerida');
        authError.classList.remove('hidden');
        authModal.classList.remove('hidden');
        return;
      }

      if (msg.type === 'auth_ok' || msg.type === 'agents_list') {
        if (msg.type === 'auth_ok') {
          localStorage.setItem('ra_admin_password', state.password);
          authModal.classList.add('hidden');
          authError.classList.add('hidden');
          setWsBadge('connected', 'En línea');
        }

        state.agents.clear();
        (msg.agents || []).forEach((a) => state.agents.set(a.agent_id, a));
        if (Array.isArray(msg.known_devices)) {
          state.knownDevices = msg.known_devices;
        }
        if (typeof msg.offline_count === 'number') {
          state.offlineCount = msg.offline_count;
        }

        populateLogDeviceSelector();
        updateCountersAndGrid();

        if (state.currentView === 'viewer' && state.selectedAgentId && state.agents.has(state.selectedAgentId)) {
          updateViewerHeader(state.agents.get(state.selectedAgentId));
          if (msg.type === 'auth_ok') {
            sendWsJson({ type: 'subscribe', agent_id: state.selectedAgentId });
          }
        }
        return;
      }

      if (msg.type === 'agent_telemetry' && msg.agent) {
        state.agents.set(msg.agent.agent_id, msg.agent);
        updateCountersAndGrid();
        if (state.selectedAgentId === msg.agent.agent_id) {
          updateViewerHeader(msg.agent);
        }
        return;
      }

      if (msg.type === 'agent_details' && msg.agent) {
        state.agents.set(msg.agent.agent_id, msg.agent);
        if (state.selectedAgentId === msg.agent.agent_id) {
          updateViewerHeader(msg.agent);
          populateMonitorsDropdown(msg.agent);
        }
        return;
      }

      if (msg.type === 'new_log' && msg.log) {
        if (state.currentView === 'logs') {
          const devFilter = logDeviceFilter.value || 'ALL';
          const catFilter = logCategoryFilter.value || 'ALL';
          const q = logSearchInput.value.trim().toLowerCase();
          const l = msg.log;
          const matchDev = devFilter === 'ALL' || l.agent_id === devFilter;
          const matchCat = catFilter === 'ALL' || l.category === catFilter;
          const matchQ = !q || `${l.message} ${l.alias} ${l.hostname}`.toLowerCase().includes(q);
          if (matchDev && matchCat && matchQ) {
            state.logsCache.unshift(l);
            if (state.logsCache.length > 250) state.logsCache.pop();
            renderLogsTable();
          }
        }
        return;
      }

      if (msg.type === 'chat_message' && msg.chat) {
        if (state.selectedAgentId === msg.chat.agent_id) {
          appendLiveChatMessage(msg.chat);
          if (chatSidebar && chatSidebar.classList.contains('hidden') && msg.chat.sender === 'agent') {
            chatUnreadCount += 1;
            if (chatUnreadBadge) {
              chatUnreadBadge.textContent = String(chatUnreadCount);
              chatUnreadBadge.classList.remove('hidden');
            }
          }
        }
        return;
      }

      if (msg.type === 'clipboard_data') {
        if (remoteClipOutput) {
          remoteClipOutput.value = msg.text || '';
        }
        return;
      }

      if (msg.type === 'file_transfer_ack') {
        if (fileStatusMsg) {
          fileStatusMsg.classList.remove('hidden', 'status-error');
          fileStatusMsg.classList.add('status-ok');
          if (msg.ok) {
            const openTxt = msg.auto_opened ? ' y fue ejecutado/abierto automáticamente' : '';
            fileStatusMsg.textContent = `Archivo guardado en la PC: ${msg.saved_path}${openTxt}.`;
          } else {
            fileStatusMsg.classList.replace('status-ok', 'status-error');
            fileStatusMsg.textContent = `Error al guardar archivo en la PC: ${msg.error || 'Error desconocido'}`;
          }
        }
        return;
      }

      if (msg.type === 'agent_disconnected') {
        state.agents.delete(msg.agent_id);
        updateCountersAndGrid();
        if (state.selectedAgentId === msg.agent_id && state.currentView === 'viewer') {
          viewerActiveWindow.textContent = 'El equipo remoto se ha desconectado. Esperando reconexión...';
          viewerLoading.classList.remove('hidden');
        }
      }
    };

    ws.onclose = () => {
      setWsBadge('disconnected', 'Reconectando...');
      setTimeout(connectAdminWebSocket, 3000);
    };
  }

  // Eventos de UI
  authForm.addEventListener('submit', (e) => {
    e.preventDefault();
    state.password = passInput.value;
    if (state.ws) {
      state.ws.close();
    } else {
      connectAdminWebSocket();
    }
  });

  aliasForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const agentId = aliasAgentIdInput.value;
    const newAlias = aliasTextInput.value.trim();
    if (agentId && newAlias) {
      sendWsJson({
        type: 'set_alias',
        agent_id: agentId,
        alias: newAlias,
      });
    }
    aliasModal.classList.add('hidden');
  });

  aliasCancelBtn.addEventListener('click', () => {
    aliasModal.classList.add('hidden');
  });

  agentsGrid.addEventListener('click', (e) => {
    const editBtn = e.target.closest('.js-edit-alias');
    if (editBtn) {
      e.stopPropagation();
      openAliasEditor(editBtn.dataset.agentId);
      return;
    }
    const chatBtn = e.target.closest('.js-open-chat');
    if (chatBtn) {
      e.stopPropagation();
      openViewerForAgent(chatBtn.dataset.agentId, { focusChat: true });
      return;
    }
    const logsBtn = e.target.closest('.js-open-logs');
    if (logsBtn) {
      e.stopPropagation();
      openLogsForAgent(logsBtn.dataset.agentId);
      return;
    }
    const openTrigger = e.target.closest('.js-open-viewer');
    if (openTrigger) {
      openViewerForAgent(openTrigger.dataset.agentId);
    }
  });

  metricsTbody.addEventListener('click', (e) => {
    const connectBtn = e.target.closest('.js-metric-connect');
    if (connectBtn) {
      openViewerForAgent(connectBtn.dataset.agentId);
      return;
    }
    const logsBtn = e.target.closest('.js-metric-logs');
    if (logsBtn) {
      openLogsForAgent(logsBtn.dataset.agentId);
    }
  });

  document.querySelectorAll('.filter-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      state.currentFilter = btn.dataset.filter || 'all';
      updateCountersAndGrid();
    });
  });

  searchInput.addEventListener('input', () => {
    state.searchQuery = searchInput.value;
    updateCountersAndGrid();
  });

  refreshThumbsBtn.addEventListener('click', () => {
    sendWsJson({ type: 'request_all_thumbnails' });
  });

  tabWall.addEventListener('click', () => switchView('wall'));
  tabViewer.addEventListener('click', () => {
    if (state.selectedAgentId) {
      openViewerForAgent(state.selectedAgentId);
    }
  });
  tabActivity.addEventListener('click', () => switchView('activity'));
  tabLogs.addEventListener('click', () => switchView('logs'));

  backToWallBtn.addEventListener('click', () => switchView('wall'));

  viewerEditAliasBtn.addEventListener('click', () => {
    if (state.selectedAgentId) {
      openAliasEditor(state.selectedAgentId);
    }
  });

  viewerLogsBtn.addEventListener('click', () => {
    if (state.selectedAgentId) {
      openLogsForAgent(state.selectedAgentId);
    }
  });

  toggleControlBtn.addEventListener('click', () => {
    viewer.toggleControlMode();
    sendWsJson({
      type: 'audit_control_mode',
      enabled: viewer.controlEnabled,
    });
  });

  monitorSelect.addEventListener('change', () => {
    const monId = Number(monitorSelect.value);
    sendWsJson({
      type: 'set_monitor',
      monitor_id: monId,
    });
  });

  qualitySelect.addEventListener('change', () => {
    const parts = qualitySelect.value.split(',');
    const quality = Number(parts[0]) || 65;
    const fps = Number(parts[1]) || 18;
    const scale = Number(parts[2]) || 1.0;
    sendWsJson({
      type: 'set_stream_config',
      quality,
      fps,
      scale,
    });
  });

  document.querySelectorAll('[data-shortcut]').forEach((btn) => {
    btn.addEventListener('click', () => {
      sendWsJson({
        type: 'shortcut',
        combo: btn.dataset.shortcut,
      });
    });
  });

  fullscreenBtn.addEventListener('click', () => {
    if (!document.fullscreenElement) {
      viewViewer.requestFullscreen().catch(() => {});
    } else {
      document.exitFullscreen().catch(() => {});
    }
  });

  // Eventos de Vista 3 (Métricas) y Vista 4 (Logs)
  metricsDateInput.addEventListener('change', loadDailyMetrics);
  refreshMetricsBtn.addEventListener('click', loadDailyMetrics);

  logDeviceFilter.addEventListener('change', fetchLogsFromServer);
  logCategoryFilter.addEventListener('change', fetchLogsFromServer);
  logSearchInput.addEventListener('input', fetchLogsFromServer);

  exportCsvBtn.addEventListener('click', () => {
    const dev = logDeviceFilter.value || 'ALL';
    const cat = logCategoryFilter.value || 'ALL';
    const q = logSearchInput.value.trim();
    const params = new URLSearchParams({ agent_id: dev, category: cat, q });
    window.location.href = `/api/logs/export-csv?${params.toString()}`;
  });

  // Eventos de Fase 3: Chat Lateral y Mensajes Rápidos
  if (toggleChatSidebarBtn && chatSidebar) {
    toggleChatSidebarBtn.addEventListener('click', () => {
      const nextOpen = chatSidebar.classList.contains('hidden');
      setChatSidebarOpen(nextOpen);
    });
  }

  if (closeChatSidebarBtn) {
    closeChatSidebarBtn.addEventListener('click', () => {
      setChatSidebarOpen(false);
    });
  }

  document.querySelectorAll('.js-quick-chat').forEach((btn) => {
    btn.addEventListener('click', () => {
      const msgText = btn.dataset.msg;
      if (msgText) {
        sendDirectChatMessage(msgText);
      }
    });
  });

  if (chatSendForm) {
    chatSendForm.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!chatInput) return;
      const text = chatInput.value.trim();
      if (text) {
        sendDirectChatMessage(text);
        chatInput.value = '';
      }
    });
  }

  // Eventos de Fase 3: Portapapeles Bidireccional
  if (openClipboardBtn && clipboardModal) {
    openClipboardBtn.addEventListener('click', () => {
      if (clipStatusMsg) clipStatusMsg.classList.add('hidden');
      clipboardModal.classList.remove('hidden');
      if (state.selectedAgentId) {
        sendWsJson({
          type: 'clipboard_get',
          agent_id: state.selectedAgentId,
        });
      }
    });
  }

  if (closeClipboardBtn && clipboardModal) {
    closeClipboardBtn.addEventListener('click', () => {
      clipboardModal.classList.add('hidden');
    });
  }

  if (readRemoteClipBtn) {
    readRemoteClipBtn.addEventListener('click', () => {
      if (state.selectedAgentId) {
        sendWsJson({
          type: 'clipboard_get',
          agent_id: state.selectedAgentId,
        });
      }
    });
  }

  if (copyLocalClipBtn && remoteClipOutput) {
    copyLocalClipBtn.addEventListener('click', async () => {
      try {
        await navigator.clipboard.writeText(remoteClipOutput.value || '');
        if (clipStatusMsg) {
          clipStatusMsg.className = 'status-feedback status-ok';
          clipStatusMsg.textContent = 'Texto remoto copiado al portapapeles de tu Mac.';
        }
      } catch (err) {
        remoteClipOutput.select();
        document.execCommand('copy');
      }
    });
  }

  if (sendClipForm) {
    sendClipForm.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!state.selectedAgentId || !sendClipInput) return;
      const text = sendClipInput.value;
      const autoPaste = Boolean(clipAutopasteCheck && clipAutopasteCheck.checked);
      sendWsJson({
        type: 'clipboard_set',
        agent_id: state.selectedAgentId,
        text,
        auto_paste: autoPaste,
      });
      if (clipStatusMsg) {
        clipStatusMsg.className = 'status-feedback status-ok';
        clipStatusMsg.textContent = autoPaste
          ? 'Texto enviado al portapapeles remoto y pegado (Ctrl+V).'
          : 'Texto copiado al portapapeles de la PC remota.';
      }
    });
  }

  // Eventos de Fase 3: Envío de Archivos (Drag & Drop + Multipart)
  function updateSelectedFileUI(file) {
    selectedUploadFile = file || null;
    if (!fileDropzoneLabel) return;
    if (selectedUploadFile) {
      const kb = Math.max(1, Math.round(selectedUploadFile.size / 1024));
      fileDropzoneLabel.textContent = `Archivo seleccionado: ${selectedUploadFile.name} (${kb} KB)`;
    } else {
      fileDropzoneLabel.textContent = 'Arrastra un archivo aquí o haz clic para seleccionarlo';
    }
  }

  if (openFileBtn && fileModal) {
    openFileBtn.addEventListener('click', () => {
      if (fileStatusMsg) fileStatusMsg.classList.add('hidden');
      fileModal.classList.remove('hidden');
    });
  }

  if (closeFileBtn && fileModal) {
    closeFileBtn.addEventListener('click', () => {
      fileModal.classList.add('hidden');
    });
  }

  if (fileDropzone && fileInput) {
    fileDropzone.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', () => {
      if (fileInput.files && fileInput.files[0]) {
        updateSelectedFileUI(fileInput.files[0]);
      }
    });
    fileDropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      fileDropzone.classList.add('dragover');
    });
    fileDropzone.addEventListener('dragleave', () => {
      fileDropzone.classList.remove('dragover');
    });
    fileDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      fileDropzone.classList.remove('dragover');
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        updateSelectedFileUI(e.dataTransfer.files[0]);
      }
    });
  }

  if (fileUploadForm) {
    fileUploadForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!state.selectedAgentId) return;
      if (!selectedUploadFile) {
        if (fileStatusMsg) {
          fileStatusMsg.className = 'status-feedback status-error';
          fileStatusMsg.textContent = 'Por favor selecciona o arrastra un archivo primero.';
        }
        return;
      }

      const fd = new FormData();
      fd.append('file', selectedUploadFile);
      fd.append('destination', fileDestinationSelect ? fileDestinationSelect.value : 'Desktop');
      fd.append('auto_open', fileAutoopenCheck && fileAutoopenCheck.checked ? 'true' : 'false');

      if (submitFileBtn) submitFileBtn.disabled = true;
      if (fileStatusMsg) {
        fileStatusMsg.className = 'status-feedback status-ok';
        fileStatusMsg.textContent = 'Enviando archivo al equipo remoto...';
      }

      try {
        const res = await fetch(`/api/send-file/${encodeURIComponent(state.selectedAgentId)}`, {
          method: 'POST',
          body: fd,
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'Error al transferir el archivo');
        }
        if (fileStatusMsg) {
          fileStatusMsg.className = 'status-feedback status-ok';
          fileStatusMsg.textContent = `Archivo '${data.filename}' transmitido a ${data.destination}. Esperando confirmación del agente...`;
        }
        updateSelectedFileUI(null);
        if (fileInput) fileInput.value = '';
      } catch (err) {
        if (fileStatusMsg) {
          fileStatusMsg.className = 'status-feedback status-error';
          fileStatusMsg.textContent = err.message || 'No se pudo enviar el archivo.';
        }
      } finally {
        if (submitFileBtn) submitFileBtn.disabled = false;
      }
    });
  }

  // Eventos de Fase 4: Generador de Paquete Instalador (.ZIP) para Windows
  const openAgentPkgBtn = document.getElementById('open-agent-pkg-btn');
  const agentPkgModal = document.getElementById('agent-pkg-modal');
  const closeAgentPkgBtn = document.getElementById('close-agent-pkg-btn');
  const agentPkgForm = document.getElementById('agent-pkg-form');
  const pkgAliasInput = document.getElementById('pkg-alias-input');
  const pkgServerUrlInput = document.getElementById('pkg-server-url-input');

  if (openAgentPkgBtn && agentPkgModal) {
    openAgentPkgBtn.addEventListener('click', async () => {
      agentPkgModal.classList.remove('hidden');
      try {
        const res = await fetch('/api/agent-package-info');
        const info = await res.json();
        if (info && info.default_ws_url && pkgServerUrlInput && !pkgServerUrlInput.dataset.userEdited) {
          pkgServerUrlInput.value = info.default_ws_url;
        }
      } catch (_) {}
    });
  }

  if (pkgServerUrlInput) {
    pkgServerUrlInput.addEventListener('input', () => {
      pkgServerUrlInput.dataset.userEdited = 'true';
    });
  }

  if (closeAgentPkgBtn && agentPkgModal) {
    closeAgentPkgBtn.addEventListener('click', () => {
      agentPkgModal.classList.add('hidden');
    });
  }

  if (agentPkgForm) {
    agentPkgForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const alias = pkgAliasInput ? pkgAliasInput.value.trim() : '';
      const serverUrl = pkgServerUrlInput ? pkgServerUrlInput.value.trim() : '';
      const params = new URLSearchParams();
      if (serverUrl) params.set('server_url', serverUrl);
      if (alias) params.set('alias', alias);
      window.location.href = `/api/download-agent?${params.toString()}`;
      if (agentPkgModal) {
        setTimeout(() => agentPkgModal.classList.add('hidden'), 400);
      }
    });
  }

  connectAdminWebSocket();
})();
