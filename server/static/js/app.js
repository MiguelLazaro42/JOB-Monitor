(function () {
  // Limpiar clave antigua sin sesión explícita para no exponer el panel automáticamente
  localStorage.removeItem('ra_admin_password');

  let savedUserObj = null;
  try {
    const rawUser = sessionStorage.getItem('ra_session_user_obj') || localStorage.getItem('ra_remember_user_obj');
    if (rawUser) savedUserObj = JSON.parse(rawUser);
  } catch (_) {}

  const state = {
    ws: null,
    username: sessionStorage.getItem('ra_session_user') || localStorage.getItem('ra_remember_user') || 'admin',
    password: sessionStorage.getItem('ra_session_pass') || localStorage.getItem('ra_remember_pass') || '',
    user: savedUserObj,
    tenants: [],
    usersList: [],
    authenticated: false,
    agents: new Map(),
    knownDevices: [],
    selectedSessionTabs: new Map(),
    offlineCount: 0,
    currentFilter: 'all',
    tenantFilter: 'ALL',
    searchQuery: '',
    currentView: 'wall',
    selectedAgentId: null,
    logsCache: [],
    timezone: 'America/Merida',
    lastWsActivityAt: Date.now(),
  };

  // Referencias DOM Generales y Login
  const appShell = document.getElementById('app-shell');
  const authModal = document.getElementById('auth-modal');
  const authForm = document.getElementById('auth-form');
  const userInput = document.getElementById('admin-user-input');
  const passInput = document.getElementById('admin-pass-input');
  const rememberSessionCheck = document.getElementById('remember-session-check');
  const loginSubmitBtn = document.getElementById('login-submit-btn');
  const authError = document.getElementById('auth-error');
  const logoutBtn = document.getElementById('logout-btn');
  const activeUserBadge = document.getElementById('active-user-badge');
  const navOnlineBadge = document.getElementById('nav-online-badge');

  if (userInput) {
    userInput.value = state.username || 'admin';
  }

  const aliasModal = document.getElementById('alias-modal');
  const aliasForm = document.getElementById('alias-form');
  const aliasAgentIdInput = document.getElementById('alias-agent-id');
  const aliasTextInput = document.getElementById('alias-input');
  const aliasCancelBtn = document.getElementById('alias-cancel-btn');

  const wsStatusPill = document.getElementById('ws-status-pill');
  const wsStatusText = document.getElementById('ws-status-text');
  const liveClockText = document.getElementById('live-clock-text');
  const timezoneSelect = document.getElementById('timezone-select');

  // Pestañas y Vistas
  const tabWall = document.getElementById('tab-wall');
  const tabViewer = document.getElementById('tab-viewer');
  const tabActivity = document.getElementById('tab-activity');
  const tabLogs = document.getElementById('tab-logs');
  const tabSettings = document.getElementById('tab-settings');
  const tabViewerLabel = document.getElementById('tab-viewer-label');

  const viewWall = document.getElementById('view-wall');
  const viewViewer = document.getElementById('view-viewer');
  const viewActivity = document.getElementById('view-activity');
  const viewLogs = document.getElementById('view-logs');
  const viewSettings = document.getElementById('view-settings');

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
  const wallTenantFilterWrap = document.getElementById('wall-tenant-filter-wrap');
  const wallTenantFilter = document.getElementById('wall-tenant-filter');

  // Vista 2: Visor
  const backToWallBtn = document.getElementById('back-to-wall-btn');
  const viewerStatusDot = document.getElementById('viewer-status-dot');
  const viewerDeviceAlias = document.getElementById('viewer-device-alias');
  const viewerEditAliasBtn = document.getElementById('viewer-edit-alias-btn');
  const viewerUnlinkBtn = document.getElementById('viewer-unlink-btn');
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

  // Vista 5: Ajustes Multi-Tenant y Modal de Usuario
  const usersTbody = document.getElementById('users-tbody');
  const settingsDevicesTbody = document.getElementById('settings-devices-tbody');
  const openCreateUserBtn = document.getElementById('open-create-user-btn');
  const createUserBtnLabel = document.getElementById('create-user-btn-label');
  const settingsUsersTitle = document.getElementById('settings-users-title');
  const refreshSettingsBtn = document.getElementById('refresh-settings-btn');
  const userModal = document.getElementById('user-modal');
  const userModalTitle = document.getElementById('user-modal-title');
  const userForm = document.getElementById('user-form');
  const userEditId = document.getElementById('user-edit-id');
  const userDisplayInput = document.getElementById('user-display-input');
  const userUsernameInput = document.getElementById('user-username-input');
  const userPasswordInput = document.getElementById('user-password-input');
  const userRoleWrap = document.getElementById('user-role-wrap');
  const userRoleSelect = document.getElementById('user-role-select');
  const userTenantWrap = document.getElementById('user-tenant-wrap');
  const userTenantSelect = document.getElementById('user-tenant-select');
  const userControlCheck = document.getElementById('user-control-check');
  const userFormError = document.getElementById('user-form-error');
  const closeUserModalBtn = document.getElementById('close-user-modal-btn');
  const pkgTenantRow = document.getElementById('pkg-tenant-row');
  const pkgTenantSelect = document.getElementById('pkg-tenant-select');
  const openAgentPkgBtn = document.getElementById('open-agent-pkg-btn');
  const macosCmdOutput = document.getElementById('macos-cmd-output');
  const copyMacosCmdBtn = document.getElementById('copy-macos-cmd-btn');
  const copyMacosCmdLabel = document.getElementById('copy-macos-cmd-label');

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

  // Función para obtener la fecha YYYY-MM-DD en la zona horaria configurada (por defecto America/Merida)
  function getTodayInTimezone(tz) {
    try {
      const parts = new Intl.DateTimeFormat('en-CA', {
        timeZone: tz || state.timezone || 'America/Merida',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
      }).formatToParts(new Date());
      const y = parts.find((p) => p.type === 'year').value;
      const m = parts.find((p) => p.type === 'month').value;
      const d = parts.find((p) => p.type === 'day').value;
      return `${y}-${m}-${d}`;
    } catch (_) {
      return new Date().toISOString().slice(0, 10);
    }
  }

  function updateLiveClock() {
    if (!liveClockText) return;
    try {
      liveClockText.textContent = new Intl.DateTimeFormat('es-MX', {
        timeZone: state.timezone || 'America/Merida',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      }).format(new Date());
    } catch (_) {
      liveClockText.textContent = new Date().toTimeString().slice(0, 8);
    }
  }

  updateLiveClock();
  setInterval(updateLiveClock, 1000);

  // Inicializar fecha de métricas en hoy (según zona horaria Mérida, MX)
  const todayIso = getTodayInTimezone(state.timezone);
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

  // Micro-Función M1: Peticiones HTTP autenticadas con X-Admin-Password
  function authFetch(url, options = {}) {
    const headers = Object.assign({}, options.headers || {}, {
      'X-Admin-Password': state.password || '',
    });
    return fetch(url, Object.assign({}, options, { headers }));
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

  // Micro-Función M2: Unificar equipos en vivo y equipos desconectados (SQLite) en el Muro
  function buildOfflineWallItems() {
    const offlineList = [];
    (state.knownDevices || []).forEach((dev) => {
      if (!state.agents.has(dev.agent_id)) {
        offlineList.push({
          agent_id: dev.agent_id,
          alias: dev.alias || dev.hostname || dev.agent_id,
          hostname: dev.hostname || 'Equipo registrado',
          username: dev.username || '---',
          local_ip: dev.local_ip || '---',
          last_seen_at: dev.last_seen_at || '---',
          tenant_id: dev.tenant_id || 'sitai-master',
          tenant_name: dev.tenant_name || 'SITAI Principal',
          activity_status: 'offline',
          is_offline_card: true,
        });
      }
    });
    return offlineList;
  }

  function canManageDevices() {
    return !state.user || state.user.role === 'superadmin' || state.user.role === 'tenant_admin';
  }

  function isSuperAdmin() {
    return Boolean(state.user && state.user.role === 'superadmin');
  }

  function updateCountersAndGrid() {
    const tFilter = state.tenantFilter || 'ALL';
    const rawOnlineList = Array.from(state.agents.values());
    const rawOfflineList = buildOfflineWallItems();

    const onlineList = rawOnlineList.filter((ag) => tFilter === 'ALL' || ag.tenant_id === tFilter);
    const offlineList = rawOfflineList.filter((ag) => tFilter === 'ALL' || ag.tenant_id === tFilter);
    const allItems = [...onlineList, ...offlineList];

    if (navOnlineBadge) {
      navOnlineBadge.textContent = String(onlineList.length);
    }

    let activeCount = 0;
    let idleCount = 0;
    let awayCount = 0;

    onlineList.forEach((ag) => {
      if (ag.activity_status === 'active') activeCount += 1;
      else if (ag.activity_status === 'idle') idleCount += 1;
      else awayCount += 1;
    });

    countTotal.textContent = String(allItems.length);
    countActive.textContent = String(activeCount);
    countIdle.textContent = String(idleCount);
    countAway.textContent = String(awayCount);
    if (countOffline) {
      countOffline.textContent = String(offlineList.length);
    }

    if (allItems.length === 0) {
      emptyState.classList.remove('hidden');
      agentsGrid.classList.add('hidden');
      return;
    }

    emptyState.classList.add('hidden');
    agentsGrid.classList.remove('hidden');

    const q = state.searchQuery.trim().toLowerCase();
    const filtered = allItems.filter((ag) => {
      if (state.currentFilter !== 'all' && ag.activity_status !== state.currentFilter) {
        return false;
      }
      if (!q) return true;
      const hay = `${ag.alias} ${ag.hostname} ${ag.username} ${ag.local_ip} ${ag.active_window || ''} ${ag.tenant_name || ''}`.toLowerCase();
      return hay.includes(q);
    });

    const showManageBtns = canManageDevices();
    const showTenantPill = isSuperAdmin();

    // Agrupación por Hardware / PC Física (soporta multi-sesión de Windows de forma elegante)
    const hardwareGroups = new Map();
    filtered.forEach((item) => {
      const rawId = item.agent_id ? item.agent_id.replace(/-S\d+$/, '') : item.agent_id;
      const groupKey = `${rawId}__${(item.hostname || '').toLowerCase()}`;
      if (!hardwareGroups.has(groupKey)) {
        hardwareGroups.set(groupKey, []);
      }
      hardwareGroups.get(groupKey).push(item);
    });

    agentsGrid.innerHTML = Array.from(hardwareGroups.entries())
      .map(([groupKey, groupItems]) => {
        // Ordenar sesiones: poner en primer lugar la sesión activa con pantalla (o la seleccionada)
        groupItems.sort((a, b) => {
          if (a.is_offline_card && !b.is_offline_card) return 1;
          if (!a.is_offline_card && b.is_offline_card) return -1;
          if (!a.is_background_session && b.is_background_session) return -1;
          if (a.is_background_session && !b.is_background_session) return 1;
          return a.agent_id.localeCompare(b.agent_id);
        });

        const storedSelectedId = state.selectedSessionTabs.get(groupKey);
        const activeItem = groupItems.find((it) => it.agent_id === storedSelectedId) || groupItems[0];
        const ag = activeItem;

        const isMultiSession = groupItems.length > 1;

        // Construir barra de pestañas superior si esta PC tiene 2 o más sesiones
        let sessionTabsHtml = '';
        if (isMultiSession) {
          const tabsButtons = groupItems
            .map((item, idx) => {
              const isCur = item.agent_id === ag.agent_id;
              const isBg = Boolean(item.is_background_session);
              const isOff = Boolean(item.is_offline_card);
              let sessionLabel = item.alias || `Sesión ${idx + 1}`;
              if (item.username && !sessionLabel.toLowerCase().includes(item.username.toLowerCase())) {
                sessionLabel = `${sessionLabel} (${item.username})`;
              }
              const tabDot = isOff
                ? 'dot-slate'
                : (item.activity_status === 'active' ? 'dot-emerald' : (item.activity_status === 'idle' ? 'dot-amber' : 'dot-rose'));

              const statePill = isOff
                ? '<span class="session-tab-pill-bg">Off</span>'
                : (isBg ? '<span class="session-tab-pill-bg">2ºP</span>' : '');

              return `
                <button type="button" class="session-tab-btn ${isCur ? 'active' : ''} js-switch-session-tab" data-group-key="${escapeHtml(groupKey)}" data-agent-id="${escapeHtml(item.agent_id)}" title="${escapeHtml(item.alias)} (${escapeHtml(item.username)})">
                  <span class="indicator-dot ${tabDot}"></span>
                  <span>${escapeHtml(sessionLabel)}</span>
                  ${statePill}
                </button>
              `;
            })
            .join('');

          sessionTabsHtml = `
            <div class="card-session-tabs-bar">
              <span class="card-session-meta-chip">
                <svg class="icon-xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:12px;height:12px;">
                  <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
                </svg>
                ${groupItems.length} Sesiones
              </span>
              ${tabsButtons}
            </div>
          `;
        }

        const tenantTagHtml = showTenantPill
          ? ` • <strong style="color:#0284c7;">${escapeHtml(ag.tenant_name || ag.tenant_id || 'Principal')}</strong>`
          : '';
        const manageIconsHtml = showManageBtns
          ? `
            <button type="button" class="icon-btn js-edit-alias" data-agent-id="${escapeHtml(ag.agent_id)}" title="Renombrar sesión actual">
              <svg class="icon-xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/>
              </svg>
            </button>
            <button type="button" class="icon-btn icon-btn-danger js-unlink-device" data-agent-id="${escapeHtml(ag.agent_id)}" data-alias="${escapeHtml(ag.alias)}" title="Desvincular o autodestruir">
              <svg class="icon-xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8">
                <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>
          `
          : '';

        if (ag.is_offline_card) {
          return `
            <article class="agent-card card-offline-minimal" data-agent-id="${escapeHtml(ag.agent_id)}">
              ${sessionTabsHtml}
              <div class="card-screen card-screen-offline">
                <div class="screen-top-badges">
                  <span class="badge-status badge-offline-overlay">
                    <span class="indicator-dot dot-slate"></span>
                    Apagado
                  </span>
                </div>
                <div class="screen-offline-placeholder">
                  <svg class="icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
                    <rect x="2" y="3" width="20" height="14" rx="2"/>
                    <line x1="8" y1="21" x2="16" y2="21"/>
                    <line x1="12" y1="17" x2="12" y2="21"/>
                  </svg>
                  <span>Visto: ${escapeHtml(ag.last_seen_at)}</span>
                </div>
              </div>

              <div class="card-body">
                <div class="card-identity-row">
                  <div class="card-alias-wrap">
                    <span class="card-alias" title="${escapeHtml(ag.alias)}">${escapeHtml(ag.alias)}</span>
                    ${manageIconsHtml}
                  </div>
                  <span class="card-ip-pill">${escapeHtml(ag.local_ip || '---')}</span>
                </div>

                <p class="card-submeta">${escapeHtml(ag.hostname)} • ${escapeHtml(ag.username)}${tenantTagHtml}</p>

                <div class="card-focus-bar focus-bar-offline">
                  <span class="focus-win-text">Sin conexión activa</span>
                  <span class="focus-time-text">Offline</span>
                </div>
              </div>

              <div class="card-footer">
                <button type="button" class="btn btn-pastel-slate card-btn-main" disabled>
                  <span>Sin señal</span>
                </button>
                <button type="button" class="btn btn-pastel-orange card-btn-side js-open-logs" data-agent-id="${escapeHtml(ag.agent_id)}" title="Ver bitácora">
                  <span>Bitácora</span>
                </button>
                ${
                  showManageBtns
                    ? `<button type="button" class="btn btn-danger-soft card-btn-side js-unlink-device" data-agent-id="${escapeHtml(ag.agent_id)}" data-alias="${escapeHtml(ag.alias)}" title="Quitar o autodestruir equipo"><span>Quitar</span></button>`
                    : ''
                }
              </div>
            </article>
          `;
        }

        const meta = getStatusMeta(ag.activity_status, ag.idle_seconds);
        const monCount = Math.max(1, (ag.monitors || []).filter((m) => m.id > 0).length);
        const thumbSrc = ag.thumbnail ? `data:image/jpeg;base64,${ag.thumbnail}` : '';
        const cardExtra = ag.activity_status === 'away' ? 'card-away' : '';
        const isBgSession = Boolean(ag.is_background_session);
        const placeholderHtml = isBgSession
          ? `<div class="screen-offline-placeholder" style="flex-direction:column; gap:6px; padding:12px; text-align:center;">
               <svg class="icon-sm" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" style="width:22px;height:22px;opacity:0.75;">
                 <rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/><path d="M7 8h10"/><path d="M7 12h6"/>
               </svg>
               <span>Sesión de Windows en 2º plano</span>
               <small style="font-size:11px; opacity:0.75;">Otro usuario tiene la pantalla activa</small>
             </div>`
          : `<div class="screen-offline-placeholder"><span>Capturando pantalla...</span></div>`;

        return `
          <article class="agent-card ${cardExtra}" data-agent-id="${escapeHtml(ag.agent_id)}">
            ${sessionTabsHtml}
            <div class="card-screen js-open-viewer" data-agent-id="${escapeHtml(ag.agent_id)}" title="Haz clic para abrir pantalla en vivo de esta sesión">
              ${
                thumbSrc
                  ? `<img src="${thumbSrc}" alt="${escapeHtml(ag.alias)}" />`
                  : placeholderHtml
              }
              <div class="screen-top-badges">
                <span class="badge-status ${meta.badgeClass}">
                  <span class="indicator-dot ${meta.dotClass}"></span>
                  ${escapeHtml(meta.label)}
                </span>
                <div style="display:flex; gap:4px; align-items:center;">
                  ${isMultiSession ? `<span class="multi-session-badge" title="Esta PC física tiene múltiples usuarios/sesiones registradas">${groupItems.length}S</span>` : ''}
                  ${isBgSession ? `<span class="screen-mon-pill" title="Esta sesión de usuario está en segundo plano en Windows">2º Plano</span>` : ''}
                  <span class="screen-mon-pill">${monCount}M</span>
                </div>
              </div>
            </div>

            <div class="card-body">
              <div class="card-identity-row">
                <div class="card-alias-wrap">
                  <span class="card-alias" title="${escapeHtml(ag.alias)}">${escapeHtml(ag.alias)}</span>
                  ${manageIconsHtml}
                </div>
                <span class="card-ip-pill">${escapeHtml(ag.local_ip || '---')}</span>
              </div>

              <p class="card-submeta">${escapeHtml(ag.hostname)} • ${escapeHtml(ag.username)}${tenantTagHtml}</p>

              <div class="card-focus-bar">
                <span class="focus-win-text" title="${escapeHtml(ag.active_window || 'Escritorio')}">${escapeHtml(ag.active_window || 'Escritorio')}</span>
                <span class="focus-time-text">${escapeHtml(formatIdleTime(ag.idle_seconds))}</span>
              </div>
            </div>

            <div class="card-footer">
              <button type="button" class="btn btn-primary card-btn-main js-open-viewer" data-agent-id="${escapeHtml(ag.agent_id)}">
                <svg class="icon-xs" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>
                </svg>
                <span>Conectar</span>
              </button>
              <button type="button" class="btn btn-pastel-green card-btn-side js-open-chat" data-agent-id="${escapeHtml(ag.agent_id)}" title="Enviar mensaje directo a esta sesión">
                <span>Chat</span>
              </button>
              <button type="button" class="btn btn-pastel-orange card-btn-side js-open-logs" data-agent-id="${escapeHtml(ag.agent_id)}" title="Ver bitácora del equipo">
                <span>Logs</span>
              </button>
              ${
                showManageBtns
                  ? `<button type="button" class="btn btn-danger-soft card-btn-side js-unlink-device" data-agent-id="${escapeHtml(ag.agent_id)}" data-alias="${escapeHtml(ag.alias)}" title="Quitar o autodestruir equipo"><span>Quitar</span></button>`
                  : ''
              }
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
    const tParam = state.tenantFilter && state.tenantFilter !== 'ALL' ? `&tenant_id=${encodeURIComponent(state.tenantFilter)}` : '';
    try {
      const res = await authFetch(`/api/metrics?date=${encodeURIComponent(dateVal)}${tParam}`);
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
    if (state.tenantFilter && state.tenantFilter !== 'ALL') {
      params.set('tenant_id', state.tenantFilter);
    }

    try {
      const res = await authFetch(`/api/logs?${params.toString()}`);
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
    viewerActiveWindow.textContent = agent.active_window || 'Escritorio';
    const shortAlias = (agent.alias || '').slice(0, 14);
    tabViewerLabel.textContent = `Visor: ${shortAlias}${(agent.alias || '').length > 14 ? '…' : ''}`;
    tabViewerLabel.title = `Visor: ${agent.alias}`;
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
      const res = await authFetch(`/api/chat/${encodeURIComponent(agentId)}?limit=100`);
      const data = await res.json();
      const list = data.messages || [];
      if (list.length === 0) {
        chatMessagesList.innerHTML = `
          <div class="chat-empty-state">
            Sin mensajes aún.
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
    tabViewer.classList.remove('hidden');
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
      settings: viewSettings,
    };
    const tabs = {
      wall: tabWall,
      viewer: tabViewer,
      activity: tabActivity,
      logs: tabLogs,
      settings: tabSettings,
    };

    Object.keys(views).forEach((k) => {
      if (views[k]) views[k].classList.toggle('hidden', k !== target);
      if (tabs[k]) tabs[k].classList.toggle('active', k === target);
    });

    if (target === 'activity') {
      loadDailyMetrics();
    } else if (target === 'logs') {
      fetchLogsFromServer();
    } else if (target === 'settings') {
      loadSettingsView();
    }
  }

  const unlinkModal = document.getElementById('unlink-modal');
  const unlinkForm = document.getElementById('unlink-form');
  const unlinkAgentIdInput = document.getElementById('unlink-agent-id');
  const unlinkDeviceName = document.getElementById('unlink-device-name');
  const unlinkCancelBtn = document.getElementById('unlink-cancel-btn');
  const unlinkConfirmBtn = document.getElementById('unlink-confirm-btn');

  function openAliasEditor(agentId) {
    const ag = state.agents.get(agentId) || (state.knownDevices || []).find((d) => d.agent_id === agentId);
    if (!ag) return;
    aliasAgentIdInput.value = agentId;
    aliasTextInput.value = ag.alias;
    aliasModal.classList.remove('hidden');
    aliasTextInput.focus();
    aliasTextInput.select();
  }

  function openUnlinkModal(agentId, alias) {
    if (!agentId || !unlinkModal) return;
    const ag = state.agents.get(agentId) || (state.knownDevices || []).find((d) => d.agent_id === agentId);
    const name = alias || (ag ? (ag.alias || ag.hostname) : agentId);
    if (unlinkAgentIdInput) unlinkAgentIdInput.value = agentId;
    if (unlinkDeviceName) unlinkDeviceName.textContent = name;
    unlinkModal.classList.remove('hidden');
  }

  function applyUserPermissions(user) {
    if (!user) return;
    state.user = user;

    if (activeUserBadge) {
      let roleLabel = 'SUPER ADMIN';
      if (user.role === 'tenant_admin') {
        roleLabel = `TENANT • ${user.tenant_name || user.display_name}`;
      } else if (user.role === 'sub_user') {
        roleLabel = `SUB-USUARIO • ${user.display_name}`;
      } else {
        roleLabel = `SUPER ADMIN • ${user.display_name || user.username}`;
      }
      activeUserBadge.textContent = roleLabel;
    }

    const isSub = user.role === 'sub_user';
    const isSuper = user.role === 'superadmin';

    if (tabSettings) {
      tabSettings.classList.toggle('hidden', isSub);
    }
    if (openAgentPkgBtn) {
      openAgentPkgBtn.classList.toggle('hidden', isSub);
    }
    if (wallTenantFilterWrap) {
      wallTenantFilterWrap.classList.toggle('hidden', !isSuper);
    }
    if (pkgTenantRow) {
      pkgTenantRow.classList.toggle('hidden', !isSuper);
    }

    if (isSub && !user.can_control) {
      if (toggleControlBtn) toggleControlBtn.classList.add('hidden');
      if (pttBtn) pttBtn.classList.add('hidden');
      if (openClipboardBtn) openClipboardBtn.classList.add('hidden');
      if (openFileBtn) openFileBtn.classList.add('hidden');
      if (viewerUnlinkBtn) viewerUnlinkBtn.classList.add('hidden');
    } else {
      if (toggleControlBtn) toggleControlBtn.classList.remove('hidden');
      if (pttBtn) pttBtn.classList.remove('hidden');
      if (openClipboardBtn) openClipboardBtn.classList.remove('hidden');
      if (openFileBtn) openFileBtn.classList.remove('hidden');
      if (viewerUnlinkBtn) viewerUnlinkBtn.classList.toggle('hidden', isSub);
    }
  }

  function populateTenantDropdowns() {
    const list = state.tenants || [];
    if (wallTenantFilter) {
      const currentVal = state.tenantFilter || 'ALL';
      let html = '<option value="ALL">Todas las cuentas (Tenants)</option>';
      list.forEach((t) => {
        const sel = t.tenant_id === currentVal ? 'selected' : '';
        html += `<option value="${escapeHtml(t.tenant_id)}" ${sel}>${escapeHtml(t.name)} (${t.devices_count || 0})</option>`;
      });
      wallTenantFilter.innerHTML = html;
    }
    if (pkgTenantSelect) {
      let html = '';
      list.forEach((t) => {
        html += `<option value="${escapeHtml(t.tenant_id)}">${escapeHtml(t.name)}</option>`;
      });
      pkgTenantSelect.innerHTML = html;
    }
    if (userTenantSelect) {
      let html = '';
      list.forEach((t) => {
        html += `<option value="${escapeHtml(t.tenant_id)}">${escapeHtml(t.name)}</option>`;
      });
      userTenantSelect.innerHTML = html;
    }
  }

  async function refreshCurrentUserProfile() {
    if (!state.password) return;
    try {
      const res = await authFetch('/api/me');
      if (res.ok) {
        const data = await res.json();
        if (data.user) {
          applyUserPermissions(data.user);
        }
        if (Array.isArray(data.tenants)) {
          state.tenants = data.tenants;
          populateTenantDropdowns();
        }
      }
    } catch (_) {}
  }

  async function loadSettingsView() {
    if (!usersTbody) return;
    try {
      const res = await authFetch('/api/users');
      if (!res.ok) return;
      const data = await res.json();
      state.usersList = data.users || [];
      state.tenants = data.tenants || [];
      populateTenantDropdowns();

      if (state.usersList.length === 0) {
        usersTbody.innerHTML = '<tr><td colspan="7" class="empty-row">No hay usuarios registrados.</td></tr>';
      } else {
        const myId = state.user ? state.user.id : 0;
        usersTbody.innerHTML = state.usersList
          .map((u) => {
            const roleLabels = {
              superadmin: 'Super Admin',
              tenant_admin: 'Usuario Principal',
              sub_user: 'Sub-usuario',
            };
            const roleClasses = {
              superadmin: 'badge-active',
              tenant_admin: 'cat-WINDOW',
              sub_user: 'badge-idle',
            };
            const permText = u.can_control ? 'Control Total' : 'Solo Lectura';
            const permClass = u.can_control ? 'text-emerald' : 'muted-text';
            const isSelf = u.id === myId;
            const canDelete = !isSelf && u.role !== 'superadmin';

            return `
              <tr>
                <td><strong>${escapeHtml(u.display_name)}</strong></td>
                <td><code>${escapeHtml(u.username)}</code></td>
                <td><span class="badge-status ${roleClasses[u.role] || 'badge-idle'}">${escapeHtml(roleLabels[u.role] || u.role)}</span></td>
                <td>${escapeHtml(u.tenant_name || u.tenant_id)}</td>
                <td><span class="${permClass}">${permText}</span></td>
                <td><strong>${u.devices_count || 0}</strong></td>
                <td>
                  <div style="display:flex; gap:6px;">
                    <button type="button" class="btn btn-secondary btn-sm js-edit-user" data-user-id="${u.id}">Editar</button>
                    ${
                      canDelete
                        ? `<button type="button" class="btn btn-danger-soft btn-sm js-delete-user" data-user-id="${u.id}" data-name="${escapeHtml(u.display_name)}">Eliminar</button>`
                        : ''
                    }
                  </div>
                </td>
              </tr>
            `;
          })
          .join('');
      }

      const devList = data.devices || [];
      if (settingsDevicesTbody) {
        if (devList.length === 0) {
          settingsDevicesTbody.innerHTML = '<tr><td colspan="5" class="empty-row">Sin equipos registrados.</td></tr>';
        } else {
          const isSuper = isSuperAdmin();
          settingsDevicesTbody.innerHTML = devList
            .map((d) => {
              const isOnline = state.agents.has(d.agent_id);
              const statusBadge = isOnline
                ? `<span class="badge-status badge-active"><span class="indicator-dot dot-emerald"></span>En línea</span>`
                : `<span class="badge-status cat-NETWORK"><span class="indicator-dot dot-slate"></span>Desconectado</span>`;

              let tenantCell = escapeHtml(d.tenant_name || d.tenant_id || 'Principal');
              if (isSuper && state.tenants.length > 0) {
                tenantCell = `
                  <select class="text-input js-change-device-tenant" data-agent-id="${escapeHtml(d.agent_id)}" style="padding:4px 8px; font-size:12px;">
                    ${state.tenants
                      .map((t) => `<option value="${escapeHtml(t.tenant_id)}" ${t.tenant_id === d.tenant_id ? 'selected' : ''}>${escapeHtml(t.name)}</option>`)
                      .join('')}
                  </select>
                `;
              }

              return `
                <tr>
                  <td><strong>${escapeHtml(d.alias || d.hostname || d.agent_id)}</strong></td>
                  <td>${escapeHtml(d.hostname)} (${escapeHtml(d.username)})</td>
                  <td>${statusBadge}</td>
                  <td>${tenantCell}</td>
                  <td>
                    <button type="button" class="btn btn-danger-soft btn-sm js-settings-unlink" data-agent-id="${escapeHtml(d.agent_id)}" data-alias="${escapeHtml(d.alias || d.hostname)}">
                      Quitar / Autodestruir
                    </button>
                  </td>
                </tr>
              `;
            })
            .join('');
        }
      }
    } catch (err) {
      console.error('Error al cargar ajustes:', err);
    }
  }

  function openCreateUserModal() {
    if (!userModal) return;
    if (userModalTitle) userModalTitle.textContent = 'Nuevo Usuario';
    if (userEditId) userEditId.value = '';
    if (userDisplayInput) userDisplayInput.value = '';
    if (userUsernameInput) {
      userUsernameInput.value = '';
      userUsernameInput.disabled = false;
    }
    if (userPasswordInput) {
      userPasswordInput.value = '';
      userPasswordInput.placeholder = 'Mínimo 4 caracteres';
      userPasswordInput.required = true;
    }
    if (userControlCheck) userControlCheck.checked = true;
    if (userFormError) userFormError.classList.add('hidden');

    const isSuper = isSuperAdmin();
    if (userRoleWrap) {
      userRoleWrap.classList.toggle('hidden', !isSuper);
    }
    if (userRoleSelect) {
      userRoleSelect.value = isSuper ? 'tenant_admin' : 'sub_user';
    }
    if (userTenantWrap) {
      userTenantWrap.classList.add('hidden');
    }
    userModal.classList.remove('hidden');
  }

  function openEditUserModal(u) {
    if (!userModal || !u) return;
    if (userModalTitle) userModalTitle.textContent = `Editar Usuario: ${u.display_name}`;
    if (userEditId) userEditId.value = String(u.id);
    if (userDisplayInput) userDisplayInput.value = u.display_name || '';
    if (userUsernameInput) {
      userUsernameInput.value = u.username || '';
      userUsernameInput.disabled = true;
    }
    if (userPasswordInput) {
      userPasswordInput.value = '';
      userPasswordInput.placeholder = '(Dejar en blanco para conservar actual)';
      userPasswordInput.required = false;
    }
    if (userControlCheck) userControlCheck.checked = Boolean(u.can_control);
    if (userFormError) userFormError.classList.add('hidden');

    if (userRoleWrap) userRoleWrap.classList.add('hidden');
    if (userTenantWrap) {
      if (isSuperAdmin() && u.role === 'sub_user') {
        userTenantWrap.classList.remove('hidden');
        if (userTenantSelect) userTenantSelect.value = u.tenant_id;
      } else {
        userTenantWrap.classList.add('hidden');
      }
    }
    userModal.classList.remove('hidden');
  }

  function showLoginGate(showError = false) {
    state.authenticated = false;
    if (appShell) appShell.classList.add('hidden');
    if (authModal) authModal.classList.remove('hidden');
    if (authError) authError.classList.toggle('hidden', !showError);
    if (passInput) passInput.focus();
  }

  function unlockDashboard() {
    state.authenticated = true;
    if (authModal) authModal.classList.add('hidden');
    if (authError) authError.classList.add('hidden');
    if (appShell) appShell.classList.remove('hidden');
  }

  function logoutAdminSession() {
    state.password = '';
    state.user = null;
    state.authenticated = false;
    sessionStorage.removeItem('ra_session_user');
    sessionStorage.removeItem('ra_session_pass');
    sessionStorage.removeItem('ra_session_user_obj');
    localStorage.removeItem('ra_remember_user');
    localStorage.removeItem('ra_remember_pass');
    localStorage.removeItem('ra_remember_user_obj');
    if (passInput) passInput.value = '';
    if (state.ws) {
      try {
        state.ws.onclose = null;
        state.ws.close();
      } catch (_) {}
      state.ws = null;
    }
    showLoginGate(false);
  }

  function connectAdminWebSocket() {
    if (!state.password) {
      showLoginGate(false);
      return;
    }
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
          username: state.username || 'admin',
          password: state.password,
        })
      );
    };

    ws.onmessage = (evt) => {
      state.lastWsActivityAt = Date.now();
      if (evt.data instanceof ArrayBuffer) {
        viewer.renderBinaryFrame(evt.data);
        return;
      }

      const msg = JSON.parse(evt.data);
      if (msg.type === 'pong') {
        if (msg.timezone && msg.timezone !== state.timezone) {
          state.timezone = msg.timezone;
          if (timezoneSelect) timezoneSelect.value = state.timezone;
          updateLiveClock();
        }
        return;
      }

      if (msg.type === 'timezone_updated' && msg.timezone) {
        state.timezone = msg.timezone;
        if (timezoneSelect) timezoneSelect.value = state.timezone;
        if (metricsDateInput) metricsDateInput.value = getTodayInTimezone(state.timezone);
        updateLiveClock();
        return;
      }

      if (msg.type === 'auth_error') {
        setWsBadge('disconnected', 'Sesión requerida');
        logoutAdminSession();
        if (authError) authError.classList.remove('hidden');
        return;
      }

      if (msg.type === 'auth_ok' || msg.type === 'agents_list') {
        if (msg.type === 'auth_ok') {
          unlockDashboard();
          setWsBadge('connected', 'En línea');
        }
        if (msg.timezone) {
          state.timezone = msg.timezone;
          if (timezoneSelect) timezoneSelect.value = state.timezone;
          updateLiveClock();
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
      if (!state.authenticated || !state.password) return;
      setWsBadge('disconnected', 'Reconectando...');
      setTimeout(connectAdminWebSocket, 3000);
    };
  }

  // Eventos de UI (Inicio y Cierre de Sesión)
  authForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const enteredUser = userInput ? userInput.value.trim() : 'admin';
    const enteredPass = passInput ? passInput.value : '';
    if (authError) authError.classList.add('hidden');
    if (loginSubmitBtn) {
      loginSubmitBtn.disabled = true;
      loginSubmitBtn.textContent = 'Verificando...';
    }

    try {
      const res = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: enteredUser, password: enteredPass }),
      });

      if (!res.ok) {
        throw new Error('Credenciales inválidas');
      }

      const data = await res.json();
      state.username = data.username || enteredUser;
      state.password = data.token || enteredPass;
      state.user = data.user || null;

      if (Array.isArray(data.tenants)) {
        state.tenants = data.tenants;
        populateTenantDropdowns();
      }

      sessionStorage.setItem('ra_session_user', state.username);
      sessionStorage.setItem('ra_session_pass', state.password);
      if (state.user) {
        sessionStorage.setItem('ra_session_user_obj', JSON.stringify(state.user));
      }

      if (rememberSessionCheck && rememberSessionCheck.checked) {
        localStorage.setItem('ra_remember_user', state.username);
        localStorage.setItem('ra_remember_pass', state.password);
        if (state.user) {
          localStorage.setItem('ra_remember_user_obj', JSON.stringify(state.user));
        }
      } else {
        localStorage.removeItem('ra_remember_user');
        localStorage.removeItem('ra_remember_pass');
        localStorage.removeItem('ra_remember_user_obj');
      }

      applyUserPermissions(state.user);
      unlockDashboard();
      if (state.ws) {
        try {
          state.ws.onclose = null;
          state.ws.close();
        } catch (_) {}
      }
      connectAdminWebSocket();
    } catch (_) {
      showLoginGate(true);
    } finally {
      if (loginSubmitBtn) {
        loginSubmitBtn.disabled = false;
        loginSubmitBtn.textContent = 'Entrar';
      }
    }
  });

  if (logoutBtn) {
    logoutBtn.addEventListener('click', () => {
      logoutAdminSession();
    });
  }

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
    const tabBtn = e.target.closest('.js-switch-session-tab');
    if (tabBtn) {
      e.stopPropagation();
      const gKey = tabBtn.dataset.groupKey;
      const targetId = tabBtn.dataset.agentId;
      if (gKey && targetId) {
        state.selectedSessionTabs.set(gKey, targetId);
        updateCountersAndGrid();
      }
      return;
    }
    const unlinkBtn = e.target.closest('.js-unlink-device');
    if (unlinkBtn) {
      e.stopPropagation();
      openUnlinkModal(unlinkBtn.dataset.agentId, unlinkBtn.dataset.alias);
      return;
    }
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
  if (tabSettings) {
    tabSettings.addEventListener('click', () => switchView('settings'));
  }

  backToWallBtn.addEventListener('click', () => switchView('wall'));

  viewerEditAliasBtn.addEventListener('click', () => {
    if (state.selectedAgentId) {
      openAliasEditor(state.selectedAgentId);
    }
  });

  if (viewerUnlinkBtn) {
    viewerUnlinkBtn.addEventListener('click', () => {
      if (state.selectedAgentId) {
        const ag = state.agents.get(state.selectedAgentId);
        openUnlinkModal(state.selectedAgentId, ag ? (ag.alias || ag.hostname) : (viewerDeviceAlias ? viewerDeviceAlias.textContent : ''));
      }
    });
  }

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
    const params = new URLSearchParams({
      agent_id: dev,
      category: cat,
      q,
      admin_pass: state.password || '',
    });
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
        const res = await authFetch(`/api/send-file/${encodeURIComponent(state.selectedAgentId)}`, {
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

  // Eventos de Fase 4: Generador de Paquete Instalador (.ZIP) y Comando Directo macOS
  const agentPkgModal = document.getElementById('agent-pkg-modal');
  const closeAgentPkgBtn = document.getElementById('close-agent-pkg-btn');
  const agentPkgForm = document.getElementById('agent-pkg-form');
  const pkgAliasInput = document.getElementById('pkg-alias-input');
  const pkgServerUrlInput = document.getElementById('pkg-server-url-input');
  let macosCmdDebounce = null;

  async function refreshMacosInstallCommand() {
    if (!macosCmdOutput || !state.password) return;
    try {
      const params = new URLSearchParams();
      if (pkgServerUrlInput && pkgServerUrlInput.value.trim()) {
        params.set('server_url', pkgServerUrlInput.value.trim());
      }
      if (pkgAliasInput && pkgAliasInput.value.trim()) {
        params.set('alias', pkgAliasInput.value.trim());
      }
      if (pkgTenantSelect && pkgTenantSelect.value) {
        params.set('tenant_id', pkgTenantSelect.value);
      }
      const res = await authFetch(`/api/macos-install-command?${params.toString()}`);
      if (res.ok) {
        const data = await res.json();
        if (data && data.command) {
          macosCmdOutput.value = data.command;
        }
      }
    } catch (_) {}
  }

  function scheduleMacosCmdRefresh() {
    if (macosCmdDebounce) clearTimeout(macosCmdDebounce);
    macosCmdDebounce = setTimeout(refreshMacosInstallCommand, 220);
  }

  if (openAgentPkgBtn && agentPkgModal) {
    openAgentPkgBtn.addEventListener('click', async () => {
      agentPkgModal.classList.remove('hidden');
      try {
        const res = await authFetch('/api/agent-package-info');
        const info = await res.json();
        if (info && info.default_ws_url && pkgServerUrlInput && !pkgServerUrlInput.dataset.userEdited) {
          pkgServerUrlInput.value = info.default_ws_url;
        }
        if (info && info.macos_command && macosCmdOutput) {
          macosCmdOutput.value = info.macos_command;
        }
        await refreshMacosInstallCommand();
      } catch (_) {}
    });
  }

  if (pkgServerUrlInput) {
    pkgServerUrlInput.addEventListener('input', () => {
      pkgServerUrlInput.dataset.userEdited = 'true';
      scheduleMacosCmdRefresh();
    });
  }

  if (pkgAliasInput) {
    pkgAliasInput.addEventListener('input', scheduleMacosCmdRefresh);
  }

  if (pkgTenantSelect) {
    pkgTenantSelect.addEventListener('change', scheduleMacosCmdRefresh);
  }

  if (copyMacosCmdBtn && macosCmdOutput) {
    copyMacosCmdBtn.addEventListener('click', async () => {
      const cmdText = (macosCmdOutput.value || '').trim();
      if (!cmdText || cmdText.startsWith('Generando')) return;
      try {
        await navigator.clipboard.writeText(cmdText);
      } catch (_) {
        macosCmdOutput.focus();
        macosCmdOutput.select();
        document.execCommand('copy');
      }
      if (copyMacosCmdLabel) {
        copyMacosCmdLabel.textContent = 'Comando copiado';
        setTimeout(() => {
          copyMacosCmdLabel.textContent = 'Copiar comando Mac';
        }, 2200);
      }
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
      const tenantId = pkgTenantSelect ? pkgTenantSelect.value : '';
      const params = new URLSearchParams();
      if (serverUrl) params.set('server_url', serverUrl);
      if (alias) params.set('alias', alias);
      if (tenantId) params.set('tenant_id', tenantId);
      if (state.password) params.set('admin_pass', state.password);
      window.location.href = `/api/download-agent?${params.toString()}`;
      if (agentPkgModal) {
        setTimeout(() => agentPkgModal.classList.add('hidden'), 400);
      }
    });
  }

  if (unlinkCancelBtn && unlinkModal) {
    unlinkCancelBtn.addEventListener('click', () => {
      unlinkModal.classList.add('hidden');
    });
  }

  if (unlinkForm) {
    unlinkForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const agentId = unlinkAgentIdInput ? unlinkAgentIdInput.value : '';
      if (!agentId) return;

      const destroyEl = document.getElementById('unlink-mode-destroy');
      const isDestroy = destroyEl ? destroyEl.checked : true;

      if (unlinkConfirmBtn) {
        unlinkConfirmBtn.disabled = true;
        unlinkConfirmBtn.textContent = 'Procesando...';
      }

      try {
        const res = await authFetch(`/api/devices/${encodeURIComponent(agentId)}?self_destruct=${isDestroy ? 'true' : 'false'}`, {
          method: 'DELETE',
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.detail || 'Error al quitar el equipo');
        }

        state.agents.delete(agentId);
        state.knownDevices = (state.knownDevices || []).filter((d) => d.agent_id !== agentId);

        if (unlinkModal) unlinkModal.classList.add('hidden');
        updateCountersAndGrid();
        populateLogDeviceSelector();

        if (state.currentView === 'viewer' && state.selectedAgentId === agentId) {
          switchView('wall');
        }
        if (state.currentView === 'settings') {
          loadSettingsView();
        }
      } catch (err) {
        alert(err.message || 'Error al desvincular o autodestruir el equipo');
      } finally {
        if (unlinkConfirmBtn) {
          unlinkConfirmBtn.disabled = false;
          unlinkConfirmBtn.textContent = 'Confirmar y Quitar Equipo';
        }
      }
    });
  }

  if (wallTenantFilter) {
    wallTenantFilter.addEventListener('change', () => {
      state.tenantFilter = wallTenantFilter.value || 'ALL';
      updateCountersAndGrid();
      if (state.currentView === 'activity') loadDailyMetrics();
      if (state.currentView === 'logs') fetchLogsFromServer();
    });
  }

  if (openCreateUserBtn) {
    openCreateUserBtn.addEventListener('click', openCreateUserModal);
  }

  if (closeUserModalBtn && userModal) {
    closeUserModalBtn.addEventListener('click', () => {
      userModal.classList.add('hidden');
    });
  }

  if (refreshSettingsBtn) {
    refreshSettingsBtn.addEventListener('click', loadSettingsView);
  }

  if (userRoleSelect) {
    userRoleSelect.addEventListener('change', () => {
      const isSub = userRoleSelect.value === 'sub_user';
      if (userTenantWrap) {
        userTenantWrap.classList.toggle('hidden', !isSub || !isSuperAdmin());
      }
    });
  }

  if (userForm) {
    userForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      if (userFormError) userFormError.classList.add('hidden');

      const editId = userEditId ? userEditId.value.trim() : '';
      const display_name = userDisplayInput ? userDisplayInput.value.trim() : '';
      const username = userUsernameInput ? userUsernameInput.value.trim() : '';
      const password = userPasswordInput ? userPasswordInput.value : '';
      const role = userRoleSelect ? userRoleSelect.value : 'sub_user';
      const tenant_id = userTenantSelect ? userTenantSelect.value : '';
      const can_control = userControlCheck ? userControlCheck.checked : true;

      try {
        if (!editId) {
          if (!username || !password || !display_name) {
            throw new Error('Todos los campos son obligatorios para crear un usuario.');
          }
          const payload = {
            username,
            password,
            display_name,
            role: isSuperAdmin() ? role : 'sub_user',
            tenant_id: isSuperAdmin() && role === 'sub_user' ? tenant_id : null,
            can_control,
          };
          const res = await authFetch('/api/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Error al crear usuario');
        } else {
          const payload = {
            display_name,
            can_control,
          };
          if (password) payload.password = password;
          if (isSuperAdmin() && tenant_id) payload.tenant_id = tenant_id;

          const res = await authFetch(`/api/users/${editId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          const data = await res.json();
          if (!res.ok) throw new Error(data.detail || 'Error al actualizar usuario');
        }

        if (userModal) userModal.classList.add('hidden');
        loadSettingsView();
      } catch (err) {
        if (userFormError) {
          userFormError.textContent = err.message || 'Error al procesar solicitud.';
          userFormError.classList.remove('hidden');
        }
      }
    });
  }

  if (usersTbody) {
    usersTbody.addEventListener('click', async (e) => {
      const editBtn = e.target.closest('.js-edit-user');
      if (editBtn) {
        const uid = Number(editBtn.dataset.userId);
        const u = (state.usersList || []).find((x) => x.id === uid);
        if (u) openEditUserModal(u);
        return;
      }
      const delBtn = e.target.closest('.js-delete-user');
      if (delBtn) {
        const uid = Number(delBtn.dataset.userId);
        const name = delBtn.dataset.name || 'este usuario';
        if (confirm(`¿Estás seguro de eliminar permanentemente al usuario "${name}"?`)) {
          try {
            const res = await authFetch(`/api/users/${uid}`, { method: 'DELETE' });
            if (!res.ok) {
              const err = await res.json();
              alert(err.detail || 'Error al eliminar usuario');
            } else {
              loadSettingsView();
            }
          } catch (err) {
            alert('Error de conexión al eliminar: ' + err.message);
          }
        }
      }
    });
  }

  if (settingsDevicesTbody) {
    settingsDevicesTbody.addEventListener('click', (e) => {
      const unlinkBtn = e.target.closest('.js-settings-unlink');
      if (unlinkBtn) {
        openUnlinkModal(unlinkBtn.dataset.agentId, unlinkBtn.dataset.alias);
      }
    });

    settingsDevicesTbody.addEventListener('change', async (e) => {
      const sel = e.target.closest('.js-change-device-tenant');
      if (sel) {
        const agentId = sel.dataset.agentId;
        const newTid = sel.value;
        try {
          const res = await authFetch(`/api/devices/${encodeURIComponent(agentId)}/tenant`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tenant_id: newTid }),
          });
          if (!res.ok) {
            const err = await res.json();
            alert(err.detail || 'Error al reasignar cuenta');
            loadSettingsView();
          } else {
            const ag = state.agents.get(agentId);
            if (ag) ag.tenant_id = newTid;
            const kd = (state.knownDevices || []).find((d) => d.agent_id === agentId);
            if (kd) kd.tenant_id = newTid;
            updateCountersAndGrid();
          }
        } catch (err) {
          alert('Error al reasignar: ' + err.message);
          loadSettingsView();
        }
      }
    });
  }

  if (timezoneSelect) {
    timezoneSelect.addEventListener('change', async () => {
      const nextTz = timezoneSelect.value || 'America/Merida';
      state.timezone = nextTz;
      updateLiveClock();
      if (metricsDateInput) {
        metricsDateInput.value = getTodayInTimezone(nextTz);
      }
      try {
        await authFetch('/api/settings/timezone', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ timezone: nextTz }),
        });
      } catch (_) {}
    });
  }

  // Watchdog del Panel Web: verifica activamente cada 12s que la sesión WebSocket esté viva
  // y la auto-reconecta de inmediato al despertar de suspensión o recuperar red.
  function checkAndRecoverConnection() {
    if (!state.authenticated || !state.password) return;
    if (!state.ws || state.ws.readyState === WebSocket.CLOSED) {
      connectAdminWebSocket();
      return;
    }
    if (state.ws.readyState === WebSocket.OPEN) {
      const idleMs = Date.now() - (state.lastWsActivityAt || Date.now());
      if (idleMs > 32000) {
        try {
          state.ws.close();
        } catch (_) {}
        connectAdminWebSocket();
      } else {
        sendWsJson({ type: 'ping' });
      }
    }
  }

  setInterval(checkAndRecoverConnection, 12000);
  window.addEventListener('online', checkAndRecoverConnection);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      checkAndRecoverConnection();
    }
  });

  if (state.password) {
    if (state.user) {
      applyUserPermissions(state.user);
    }
    unlockDashboard();
    connectAdminWebSocket();
    refreshCurrentUserProfile();
  } else {
    showLoginGate(false);
  }
})();
