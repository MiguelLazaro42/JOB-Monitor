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
    groupConsecutiveLogs: true,
    timezone: 'America/Merida',
    lastWsActivityAt: Date.now(),
  };

  if (state.password) {
    try {
      document.cookie = `ra_admin_pass=${encodeURIComponent(state.password)}; path=/; max-age=2592000; SameSite=Lax`;
    } catch (_) {}
  }

  // Inyección defensiva de estilos críticos para el modal de evidencia y chips <kbd> (evita fallos por caché intermedia)
  (function injectKeyboardAuditStyles() {
    if (document.getElementById('kb-audit-fallback-css')) return;
    const st = document.createElement('style');
    st.id = 'kb-audit-fallback-css';
    st.textContent = `
      .modal-close-icon {
        margin-left: auto;
        width: 34px;
        height: 34px;
        border-radius: 9px;
        border: 1px solid #dce3ec;
        background: #f3f6fa;
        color: #52657a;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        flex-shrink: 0;
        transition: all 0.15s ease;
        padding: 0;
      }
      .modal-close-icon:hover {
        background: #fee2e2;
        border-color: #fecaca;
        color: #e11d48;
      }
      .modal-close-icon svg {
        width: 16px;
        height: 16px;
        display: block;
        flex-shrink: 0;
      }
      .kb-transcript-stack {
        display: flex;
        flex-direction: column;
        gap: 8px;
        margin-top: 4px;
      }
      .kb-clean-box {
        background: #ffffff;
        border: 1px solid #ddd6fe;
        border-left: 3.5px solid #7c3aed;
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 13px;
        font-weight: 500;
        color: #133447;
        line-height: 1.55;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .kb-raw-box {
        background: #f8fafc;
        border: 1px solid #dce3ec;
        border-radius: 8px;
        padding: 7px 11px;
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        font-size: 12px;
        color: #334155;
        line-height: 1.75;
        white-space: pre-wrap;
        word-break: break-word;
      }
      .kb-box-caption {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        color: #52657a;
        margin-bottom: 3px;
      }
      .kb-key {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 1px 7px;
        margin: 1px 2px;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        font-size: 11px;
        font-weight: 700;
        background: linear-gradient(180deg, #ffffff 0%, #f1f5f9 100%);
        color: #334155;
        border: 1px solid #cbd5e1;
        border-bottom: 2px solid #94a3b8;
        border-radius: 5px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06);
        line-height: 1.4;
        vertical-align: baseline;
        white-space: nowrap;
      }
      .kb-key svg {
        width: 11px;
        height: 11px;
        stroke-width: 2.2;
        flex-shrink: 0;
        display: inline-block;
      }
      .kb-key-mult {
        font-style: normal;
        font-size: 10px;
        font-weight: 800;
        padding: 0 4px;
        margin-left: 1px;
        border-radius: 999px;
        background: rgba(15, 23, 42, 0.09);
        color: inherit;
      }
      .kb-key.kb-key-delete {
        background: linear-gradient(180deg, #fff1f2 0%, #ffe4e6 100%);
        color: #be123c;
        border-color: #fda4af;
        border-bottom-color: #f43f5e;
      }
      .kb-key.kb-key-action {
        background: linear-gradient(180deg, #eff6ff 0%, #dbeafe 100%);
        color: #1d4ed8;
        border-color: #93c5fd;
        border-bottom-color: #3b82f6;
      }
      .kb-key.kb-key-nav {
        background: linear-gradient(180deg, #f5f3ff 0%, #ede9fe 100%);
        color: #6d28d9;
        border-color: #c4b5fd;
        border-bottom-color: #8b5cf6;
      }
      .kb-key.kb-key-shortcut {
        background: linear-gradient(180deg, #ecfdf5 0%, #d1fae5 100%);
        color: #047857;
        border-color: #6ee7b7;
        border-bottom-color: #10b981;
      }
      .kb-key.kb-key-mod {
        background: linear-gradient(180deg, #f8fafc 0%, #e2e8f0 100%);
        color: #475569;
        border-color: #cbd5e1;
        border-bottom-color: #64748b;
      }
    `;
    document.head.appendChild(st);
  })();

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
  const tabNetwork = document.getElementById('tab-network');
  const tabSettings = document.getElementById('tab-settings');
  const tabViewerLabel = document.getElementById('tab-viewer-label');

  const viewWall = document.getElementById('view-wall');
  const viewViewer = document.getElementById('view-viewer');
  const viewActivity = document.getElementById('view-activity');
  const viewLogs = document.getElementById('view-logs');
  const viewNetwork = document.getElementById('view-network');
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
  const logsCounterBadge = document.getElementById('logs-counter-badge');
  const groupLogsToggleBtn = document.getElementById('group-logs-toggle-btn');
  const groupLogsLabel = document.getElementById('group-logs-label');

  // Modal de Evidencia Visual de Actividad de Teclado (MF-08)
  const evidenceModal = document.getElementById('evidence-modal');
  const closeEvidenceModalBtn = document.getElementById('close-evidence-modal-btn');
  const evidenceModalCloseBtn = document.getElementById('evidence-modal-close-btn');
  const evidenceModalTitle = document.getElementById('evidence-modal-title');
  const evidenceModalSubtitle = document.getElementById('evidence-modal-subtitle');
  const evidenceMetaContainer = document.getElementById('evidence-meta-container');
  const evidenceImagePreview = document.getElementById('evidence-image-preview');
  const evidenceDownloadLink = document.getElementById('evidence-download-link');
  const evidencePrevBtn = document.getElementById('evidence-prev-btn');
  const evidenceNextBtn = document.getElementById('evidence-next-btn');
  const evidencePosBadge = document.getElementById('evidence-pos-badge');
  const evidenceStagePrev = document.getElementById('evidence-stage-prev');
  const evidenceStageNext = document.getElementById('evidence-stage-next');
  const evidenceOriginBadge = document.getElementById('evidence-origin-badge');
  const evidenceNoImage = document.getElementById('evidence-no-image');
  const evidenceHdBtn = document.getElementById('evidence-hd-btn');
  const evidenceHdLabel = document.getElementById('evidence-hd-label');

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

    function computeHardwareGroupKey(item) {
      const hostClean = (item.hostname || '').trim().toLowerCase();
      const ipClean = (item.local_ip || '').trim().toLowerCase();
      if (hostClean && !['equipo registrado', 'pc-desconocida', 'unknown', '---'].includes(hostClean)) {
        const ipPart = ipClean && ipClean !== '---' ? ipClean : 'lan';
        return `hw__${hostClean}__${ipPart}`;
      }
      if (item.agent_id) {
        return `${item.agent_id.replace(/-S\d+$/, '')}__${hostClean}`;
      }
      return 'unknown';
    }

    let activeCount = 0;
    let idleCount = 0;
    let awayCount = 0;
    let onlineGroupsCount = 0;
    let offlineGroupsCount = 0;

    // Recalcular contadores por equipo físico (y no duplicar por cada usuario interno de la misma PC)
    const allHardwareGroups = new Map();
    allItems.forEach((item) => {
      const gKey = computeHardwareGroupKey(item);
      if (!allHardwareGroups.has(gKey)) {
        allHardwareGroups.set(gKey, []);
      }
      allHardwareGroups.get(gKey).push(item);
    });

    allHardwareGroups.forEach((gList) => {
      const onlineMembers = gList.filter((x) => !x.is_offline_card);
      if (onlineMembers.length === 0) {
        offlineGroupsCount += 1;
      } else {
        onlineGroupsCount += 1;
        if (onlineMembers.some((x) => x.activity_status === 'active')) activeCount += 1;
        else if (onlineMembers.some((x) => x.activity_status === 'idle')) idleCount += 1;
        else awayCount += 1;
      }
    });

    if (navOnlineBadge) {
      navOnlineBadge.textContent = String(onlineGroupsCount);
    }
    countTotal.textContent = String(allHardwareGroups.size);
    countActive.textContent = String(activeCount);
    countIdle.textContent = String(idleCount);
    countAway.textContent = String(awayCount);
    if (countOffline) {
      countOffline.textContent = String(offlineGroupsCount);
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

    // Agrupación por Hardware / PC Física (soporta multi-sesión / multi-usuario en la misma PC dentro de una sola tarjeta)
    const hardwareGroups = new Map();
    filtered.forEach((item) => {
      const groupKey = computeHardwareGroupKey(item);
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

        // Construir barra de pestañas superior si esta PC tiene 2 o más sesiones / usuarios
        let sessionTabsHtml = '';
        if (isMultiSession) {
          const allSameAlias = groupItems.every(
            (it) => (it.alias || '').trim().toLowerCase() === (groupItems[0].alias || '').trim().toLowerCase()
          );
          const tabsButtons = groupItems
            .map((item, idx) => {
              const isCur = item.agent_id === ag.agent_id;
              const isBg = Boolean(item.is_background_session);
              const isOff = Boolean(item.is_offline_card);
              let sessionLabel = item.alias || `Usuario ${idx + 1}`;
              if (allSameAlias && item.username && item.username !== '---') {
                sessionLabel = `Usuario: ${item.username}`;
              } else if (item.username && item.username !== '---' && !sessionLabel.toLowerCase().includes(item.username.toLowerCase())) {
                sessionLabel = `${sessionLabel} (${item.username})`;
              }
              const tabDot = isOff
                ? 'dot-slate'
                : (item.activity_status === 'active' ? 'dot-emerald' : (item.activity_status === 'idle' ? 'dot-amber' : 'dot-rose'));

              const statePill = isOff
                ? '<span class="session-tab-pill-bg">Off</span>'
                : (isBg ? '<span class="session-tab-pill-bg">2ºP</span>' : '');

              return `
                <button type="button" class="session-tab-btn ${isCur ? 'active' : ''} js-switch-session-tab" data-group-key="${escapeHtml(groupKey)}" data-agent-id="${escapeHtml(item.agent_id)}" title="${escapeHtml(item.alias)} • Usuario SO: ${escapeHtml(item.username)}">
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
                  <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
                </svg>
                ${groupItems.length} Usuarios
              </span>
              ${tabsButtons}
            </div>
          `;
        }

        const tenantTagHtml = showTenantPill
          ? ` <span class="card-tenant-chip">${escapeHtml(ag.tenant_name || ag.tenant_id || 'Principal')}</span>`
          : '';
        const isMacOs = /darwin|mac/i.test(String(ag.os || ''));
        const osLabel = isMacOs ? 'macOS' : (ag.os ? 'Win' : '');
        const moreMenuHtml = showManageBtns
          ? `<details class="card-more-menu">
               <summary class="card-more-trigger" title="Opciones del equipo" aria-label="Opciones del equipo">
                 <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>
               </summary>
               <div class="card-more-popover">
                 <button type="button" class="card-more-item js-edit-alias" data-agent-id="${escapeHtml(ag.agent_id)}">
                   <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
                   <span>Cambiar alias</span>
                 </button>
                 <button type="button" class="card-more-item card-more-danger js-unlink-device" data-agent-id="${escapeHtml(ag.agent_id)}" data-alias="${escapeHtml(ag.alias)}">
                   <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>
                   <span>Quitar equipo...</span>
                 </button>
               </div>
             </details>`
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
                  ${osLabel ? `<span class="screen-mon-pill">${osLabel}</span>` : ''}
                </div>
                <div class="screen-offline-placeholder">
                  <svg class="icon-md" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6">
                    <rect x="2" y="3" width="20" height="14" rx="2"/>
                    <line x1="8" y1="21" x2="16" y2="21"/>
                    <line x1="12" y1="17" x2="12" y2="21"/>
                  </svg>
                  <span>Ultima conexion: ${escapeHtml(ag.last_seen_at)}</span>
                </div>
              </div>

              <div class="card-body">
                <div class="card-identity-row">
                  <span class="card-alias" title="${escapeHtml(ag.alias)}">${escapeHtml(ag.alias)}</span>
                  <div class="card-id-actions">
                    <span class="card-ip-pill">${escapeHtml(ag.local_ip || '---')}</span>
                    ${moreMenuHtml}
                  </div>
                </div>

                <p class="card-submeta">${escapeHtml(ag.username)} · ${escapeHtml(ag.hostname)}${tenantTagHtml}</p>

                <div class="card-focus-bar focus-bar-offline">
                  <span class="focus-win-text">Equipo fuera de linea</span>
                  <span class="focus-time-text">Offline</span>
                </div>
              </div>

              <div class="card-footer">
                <button type="button" class="btn btn-pastel-slate card-btn-main" disabled>
                  <span>Sin conexion</span>
                </button>
                <div class="card-quick-dock">
                  <button type="button" class="card-dock-btn js-open-logs" data-agent-id="${escapeHtml(ag.agent_id)}" title="Consultar bitacora historica">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg>
                  </button>
                </div>
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

        const canCtrl = !state.user || state.user.can_control !== false;
        return `
          <article class="agent-card ${cardExtra}" data-agent-id="${escapeHtml(ag.agent_id)}">
            ${sessionTabsHtml}
            <div class="card-screen js-open-viewer" data-agent-id="${escapeHtml(ag.agent_id)}" title="Clic para abrir pantalla en vivo">
              ${
                thumbSrc
                  ? `<img src="${thumbSrc}" alt="${escapeHtml(ag.alias)}" />`
                  : placeholderHtml
              }
              <div class="card-connect-overlay">
                <span class="card-connect-pill">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>
                  <span>Pantalla en vivo</span>
                </span>
              </div>
              <div class="screen-top-badges">
                <span class="badge-status ${meta.badgeClass}">
                  <span class="indicator-dot ${meta.dotClass}"></span>
                  ${escapeHtml(meta.label)}
                </span>
                <div style="display:flex; gap:4px; align-items:center;">
                  ${isMultiSession ? `<span class="multi-session-badge" title="PC con multiples sesiones">${groupItems.length}S</span>` : ''}
                  ${isBgSession ? `<span class="screen-mon-pill" title="Sesion en segundo plano">2º Plano</span>` : ''}
                  <span class="screen-mon-pill">${osLabel ? `${osLabel} · ` : ''}${monCount}M</span>
                </div>
              </div>
            </div>

            <div class="card-body">
              <div class="card-identity-row">
                <span class="card-alias" title="${escapeHtml(ag.alias)}">${escapeHtml(ag.alias)}</span>
                <div class="card-id-actions">
                  <span class="card-ip-pill">${escapeHtml(ag.local_ip || '---')}</span>
                  ${moreMenuHtml}
                </div>
              </div>

              <p class="card-submeta">${escapeHtml(ag.username)} · ${escapeHtml(ag.hostname)}${tenantTagHtml}</p>

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
              <div class="card-quick-dock" role="group" aria-label="Herramientas rapidas">
                ${
                  canCtrl
                    ? `<button type="button" class="card-dock-btn js-open-finder" data-agent-id="${escapeHtml(ag.agent_id)}" title="Explorador de archivos"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></button>
                       <button type="button" class="card-dock-btn js-open-terminal" data-agent-id="${escapeHtml(ag.agent_id)}" title="Consola inversa silenciosa"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg></button>`
                    : ''
                }
                <button type="button" class="card-dock-btn js-open-chat" data-agent-id="${escapeHtml(ag.agent_id)}" title="Chat directo con el usuario">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                </button>
                <button type="button" class="card-dock-btn js-open-logs" data-agent-id="${escapeHtml(ag.agent_id)}" title="Bitacora de eventos del equipo">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg>
                </button>
              </div>
            </div>
          </article>
        `;
      })
      .join('');
  }

  function populateLogDeviceSelector() {
    const currentVal = logDeviceFilter.value || 'ALL';
    const mapDevs = new Map();
    state.knownDevices.forEach((d) => mapDevs.set(d.agent_id, { alias: d.alias, username: d.username, hostname: d.hostname }));
    state.agents.forEach((a) => mapDevs.set(a.agent_id, { alias: a.alias, username: a.username, hostname: a.hostname }));

    const aliasCounts = new Map();
    mapDevs.forEach((info) => {
      const k = (info.alias || '').trim().toLowerCase();
      aliasCounts.set(k, (aliasCounts.get(k) || 0) + 1);
    });

    let html = '<option value="ALL">Todos los equipos</option>';
    mapDevs.forEach((info, id) => {
      const sel = id === currentVal ? 'selected' : '';
      const k = (info.alias || '').trim().toLowerCase();
      const label =
        (aliasCounts.get(k) || 0) > 1 && info.username && info.username !== '---'
          ? `${info.alias} (${info.username})`
          : info.alias;
      html += `<option value="${escapeHtml(id)}" ${sel}>${escapeHtml(label)}</option>`;
    });
    logDeviceFilter.innerHTML = html;
  }

  const stDeviceSelect = document.getElementById('st-device-select');
  const stPeriodDayBtn = document.getElementById('st-period-day');
  const stPeriodYesterdayBtn = document.getElementById('st-period-yesterday');
  const stPeriod7dBtn = document.getElementById('st-period-7d');
  const stPeriodBtns = [stPeriodDayBtn, stPeriodYesterdayBtn, stPeriod7dBtn].filter(Boolean);
  const stDatePicker = document.getElementById('st-date-picker');
  const stRefreshBtn = document.getElementById('st-refresh-btn');
  const stKpiActive = document.getElementById('st-kpi-active');
  const stKpiDelta = document.getElementById('st-kpi-delta');
  const stKpiIdle = document.getElementById('st-kpi-idle');
  const stKpiSwitches = document.getElementById('st-kpi-switches');
  const stKpiScore = document.getElementById('st-kpi-score');
  const stKpiScoreBadge = document.getElementById('st-kpi-score-badge');
  const stKpiScoreFill = document.getElementById('st-kpi-score-fill');
  const stSubtitleLabel = document.getElementById('st-subtitle-label');
  const stHourlyChart = document.getElementById('st-hourly-chart');
  const stHourTooltip = document.getElementById('st-hour-tooltip');
  const stPeakHour = document.getElementById('st-peak-hour');
  const stCategoryBar = document.getElementById('st-category-bar');
  const stCategoryLegend = document.getElementById('st-category-legend');
  const stTopWork = document.getElementById('st-top-work');
  const stTopDistractions = document.getElementById('st-top-distractions');
  const stAppSearch = document.getElementById('st-app-search');
  const stCatFilterChips = document.getElementById('st-cat-filter-chips');
  const stAppsCount = document.getElementById('st-apps-count');
  const stAppsList = document.getElementById('st-apps-list');
  // Periodo enviado al backend: 'day' | '7d'. 'Ayer' se resuelve en cliente como day + fecha anterior.
  let stCurrentPeriod = 'day';
  let stSelectedAgentId = 'ALL';
  let stLoadSeq = 0;
  let stLastApps = [];
  let stLastHourly = [];
  let stSearchQuery = '';
  let stCatFilter = 'ALL';

  const ST_CAT_COLORS = {
    'Productividad y Gestion': '#007aff',
    'Desarrollo y Sistemas': '#5856d6',
    'Comunicacion': '#10b981',
    'Diseno y Creatividad': '#f87b02',
    'Navegacion General': '#0ea5e9',
    'Entretenimiento y Redes': '#e11d48',
  };
  const ST_PRODUCTIVE_CATS = new Set(['Productividad y Gestion', 'Desarrollo y Sistemas', 'Comunicacion', 'Diseno y Creatividad']);
  const ST_DISTRACTOR_CATS = new Set(['Entretenimiento y Redes']);
  const ST_WORKDAY_START = 8;
  const ST_WORKDAY_END = 18; // exclusivo: columnas 08h..17h

  function getCategoryColor(cat) {
    return ST_CAT_COLORS[cat] || '#64748b';
  }

  function stShiftIsoDate(iso, days) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso || '');
    if (!m) return iso;
    const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
    d.setUTCDate(d.getUTCDate() + days);
    return d.toISOString().slice(0, 10);
  }

  function stGetSelectedDate() {
    return (metricsDateInput && metricsDateInput.value) || (stDatePicker && stDatePicker.value) || getTodayInTimezone(state.timezone);
  }

  // Sincroniza ambos selectores de fecha (#st-date-picker <-> #metrics-date-input).
  function stSetSelectedDate(iso) {
    if (!iso) return;
    if (metricsDateInput && metricsDateInput.value !== iso) metricsDateInput.value = iso;
    if (stDatePicker && stDatePicker.value !== iso) stDatePicker.value = iso;
  }

  function stSyncPeriodButtons() {
    const today = getTodayInTimezone(state.timezone);
    const sel = stGetSelectedDate();
    let activeKey = null;
    if (stCurrentPeriod === '7d') activeKey = '7d';
    else if (sel === today) activeKey = 'day';
    else if (sel === stShiftIsoDate(today, -1)) activeKey = 'yesterday';
    stPeriodBtns.forEach((b) => {
      const on = b.dataset.period === activeKey;
      b.classList.toggle('active', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  async function loadScreenTimeAnalytics() {
    if (!stAppsList) return;
    const dateVal = stGetSelectedDate();
    stSetSelectedDate(dateVal);
    stSyncPeriodButtons();
    const agId = (stDeviceSelect && stDeviceSelect.value) || stSelectedAgentId || 'ALL';
    stSelectedAgentId = agId;
    const tParam = state.tenantFilter && state.tenantFilter !== 'ALL' ? `&tenant_id=${encodeURIComponent(state.tenantFilter)}` : '';
    const seq = ++stLoadSeq;
    if (stRefreshBtn) stRefreshBtn.classList.add('is-loading');
    try {
      const url = `/api/screen-time?agent_id=${encodeURIComponent(agId)}&date=${encodeURIComponent(dateVal)}&period=${encodeURIComponent(stCurrentPeriod)}${tParam}`;
      const res = await authFetch(url);
      const data = await res.json();
      if (seq !== stLoadSeq) return; // respuesta obsoleta
      if (!data || !data.ok) return;

      if (stDeviceSelect && Array.isArray(data.devices)) {
        let optHtml = '<option value="ALL">Todos los equipos</option>';
        data.devices.forEach((d) => {
          const sel = d.agent_id === agId ? 'selected' : '';
          const lbl = d.username && d.username !== '---' ? `${d.alias} (${d.username})` : d.alias;
          optHtml += `<option value="${escapeHtml(d.agent_id)}" ${sel}>${escapeHtml(lbl)}</option>`;
        });
        stDeviceSelect.innerHTML = optHtml;
      }
      renderScreenTimePanel(data);
    } catch (err) {
      console.error('Error al cargar Tiempo en Pantalla:', err);
    } finally {
      if (seq === stLoadSeq && stRefreshBtn) stRefreshBtn.classList.remove('is-loading');
    }
  }

  function stComputeFocusScore(cats) {
    let prod = 0;
    let dist = 0;
    (cats || []).forEach((c) => {
      const sec = Number(c.seconds) || 0;
      if (ST_PRODUCTIVE_CATS.has(c.category)) prod += sec;
      else if (ST_DISTRACTOR_CATS.has(c.category)) dist += sec;
    });
    const total = prod + dist;
    return { prod, dist, score: total > 0 ? Math.round((prod * 100) / total) : null };
  }

  function stRenderFocusScore(cats) {
    const { prod, dist, score } = stComputeFocusScore(cats);
    if (stKpiScore) {
      stKpiScore.textContent = score === null ? '---' : `${score}%`;
      stKpiScore.className = 'st-kpi-value ' + (score === null ? 'st-kpi-muted' : score >= 80 ? 'st-score-high' : score >= 60 ? 'st-score-mid' : 'st-score-low');
    }
    if (stKpiScoreBadge) {
      let cls = 'st-score-none';
      let lbl = 'Sin datos';
      if (score !== null) {
        if (score >= 80) { cls = 'st-score-high'; lbl = 'Alto Enfoque'; }
        else if (score >= 60) { cls = 'st-score-mid'; lbl = 'Moderado'; }
        else { cls = 'st-score-low'; lbl = 'Disperso'; }
      }
      stKpiScoreBadge.className = `st-score-badge ${cls}`;
      stKpiScoreBadge.textContent = lbl;
      stKpiScoreBadge.title = `Productivo: ${formatAccumulatedTime(prod)} | Distractores: ${formatAccumulatedTime(dist)}`;
    }
    if (stKpiScoreFill) {
      stKpiScoreFill.style.width = `${score === null ? 0 : score}%`;
      stKpiScoreFill.className = 'st-score-meter-fill ' + (score === null ? '' : score >= 80 ? 'st-score-high' : score >= 60 ? 'st-score-mid' : 'st-score-low');
    }
  }

  function stRenderHourly(hourly) {
    if (!stHourlyChart) return;
    stLastHourly = hourly;
    const maxTot = Math.max(60, ...hourly.map((h) => (h.active_seconds || 0) + (h.idle_seconds || 0)));
    let peak = null;
    hourly.forEach((h) => {
      if ((h.active_seconds || 0) > 0 && (!peak || h.active_seconds > peak.active_seconds)) peak = h;
    });
    stHourlyChart.innerHTML = hourly
      .map((h, idx) => {
        const actPct = Math.min(100, Math.round(((h.active_seconds || 0) * 100) / maxTot));
        const idlPct = Math.min(100 - actPct, Math.round(((h.idle_seconds || 0) * 100) / maxTot));
        const hh = String(h.hour).padStart(2, '0');
        const ariaTip = `${hh}:00 - Activo: ${formatAccumulatedTime(h.active_seconds || 0)}, Inactivo: ${formatAccumulatedTime(h.idle_seconds || 0)}, Cambios: ${h.switches_count || 0}`;
        const showLbl = h.hour % 3 === 0 ? hh : '';
        const cls = ['st-hour-col'];
        if (h.hour >= ST_WORKDAY_START && h.hour < ST_WORKDAY_END) cls.push('st-hour-workday');
        if (peak && peak.hour === h.hour) cls.push('st-hour-peak');
        return `<div class="${cls.join(' ')}" data-idx="${idx}" tabindex="0" aria-label="${escapeHtml(ariaTip)}">
            <div class="st-hour-stack">
              <div class="st-bar-idle" style="height:${idlPct}%"></div>
              <div class="st-bar-active" style="height:${actPct}%"></div>
            </div>
            <span class="st-hour-lbl">${showLbl}</span>
          </div>`;
      })
      .join('');

    if (stPeakHour) {
      const txt = stPeakHour.querySelector('.st-peak-text');
      if (peak) {
        const hh = String(peak.hour).padStart(2, '0');
        if (txt) txt.textContent = `Hora pico: ${hh}:00 (${formatAccumulatedTime(peak.active_seconds)} activo)`;
        stPeakHour.classList.remove('st-peak-empty');
      } else {
        if (txt) txt.textContent = 'Hora pico: sin actividad registrada';
        stPeakHour.classList.add('st-peak-empty');
      }
    }
  }

  function stShowHourTooltip(col) {
    if (!stHourTooltip || !col) return;
    const h = stLastHourly[Number(col.dataset.idx)];
    if (!h) return;
    const hh = String(h.hour).padStart(2, '0');
    const act = h.active_seconds || 0;
    const idl = h.idle_seconds || 0;
    const tot = act + idl;
    const actPct = tot > 0 ? Math.round((act * 100) / tot) : 0;
    const workday = h.hour >= ST_WORKDAY_START && h.hour < ST_WORKDAY_END;
    stHourTooltip.innerHTML = `
      <div class="st-tt-head"><strong>${hh}:00 - ${hh}:59</strong>${workday ? '<span class="st-tt-tag">Jornada</span>' : ''}</div>
      <div class="st-tt-row"><span><i class="st-dot st-dot-active"></i>Activo</span><strong>${escapeHtml(formatAccumulatedTime(act))}</strong></div>
      <div class="st-tt-row"><span><i class="st-dot st-dot-idle"></i>Inactivo</span><strong>${escapeHtml(formatAccumulatedTime(idl))}</strong></div>
      <div class="st-tt-row"><span>Cambios de app</span><strong>${h.switches_count || 0}</strong></div>
      ${tot > 0 ? `<div class="st-tt-foot">${actPct}% del tiempo registrado fue activo</div>` : ''}`;
    stHourTooltip.classList.remove('hidden');
    const host = stHourTooltip.offsetParent || stHourlyChart.parentElement;
    const hostRect = host.getBoundingClientRect();
    const colRect = col.getBoundingClientRect();
    const ttW = stHourTooltip.offsetWidth;
    const ttH = stHourTooltip.offsetHeight;
    let left = colRect.left - hostRect.left + colRect.width / 2 - ttW / 2;
    left = Math.max(6, Math.min(left, hostRect.width - ttW - 6));
    const top = Math.max(4, colRect.top - hostRect.top - ttH - 8);
    stHourTooltip.style.left = `${left}px`;
    stHourTooltip.style.top = `${top}px`;
  }

  function stHideHourTooltip() {
    if (stHourTooltip) stHourTooltip.classList.add('hidden');
  }

  function stRenderTopList(container, apps, emptyMsg) {
    if (!container) return;
    if (apps.length === 0) {
      container.innerHTML = `<p class="muted-text st-top-empty">${escapeHtml(emptyMsg)}</p>`;
      return;
    }
    const maxSec = Math.max(1, ...apps.map((a) => a.total_seconds || 0));
    container.innerHTML = apps
      .map((ap, i) => {
        const pct = Math.max(3, Math.round(((ap.total_seconds || 0) * 100) / maxSec));
        const col = getCategoryColor(ap.category);
        return `<button type="button" class="st-top-row js-st-top-item" data-app="${escapeHtml(ap.app_name)}" title="Filtrar explorador por ${escapeHtml(ap.app_name)}">
          <span class="st-top-rank">${i + 1}</span>
          <span class="st-top-info">
            <span class="st-top-line">
              <span class="st-top-name">${escapeHtml(ap.app_name)}</span>
              <span class="st-top-time">${escapeHtml(formatAccumulatedTime(ap.total_seconds || 0))}</span>
            </span>
            <span class="st-top-cat"><i class="st-dot" style="background:${col}"></i>${escapeHtml(ap.category || 'Sin categoria')}</span>
            <span class="st-top-bar"><span class="st-top-bar-fill" style="width:${pct}%; background:${col}"></span></span>
          </span>
        </button>`;
      })
      .join('');
  }

  function stRenderTopCompare(apps) {
    const sorted = [...apps].sort((a, b) => (b.total_seconds || 0) - (a.total_seconds || 0));
    stRenderTopList(stTopWork, sorted.filter((a) => ST_PRODUCTIVE_CATS.has(a.category)).slice(0, 5), 'Sin herramientas productivas registradas');
    stRenderTopList(stTopDistractions, sorted.filter((a) => ST_DISTRACTOR_CATS.has(a.category)).slice(0, 5), 'Sin distractores registrados');
  }

  function stNormalize(str) {
    return String(str || '')
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase();
  }

  // Resalta coincidencias escapando cada fragmento por separado (seguro frente a XSS).
  function stHighlight(text, q) {
    const raw = String(text || '');
    if (!q) return escapeHtml(raw);
    const norm = stNormalize(raw);
    // NFD + quitar diacriticos conserva la longitud para caracteres latinos precompuestos.
    if (norm.length !== raw.length) return escapeHtml(raw);
    let out = '';
    let pos = 0;
    let hit = norm.indexOf(q, pos);
    while (hit !== -1) {
      out += escapeHtml(raw.slice(pos, hit)) + `<mark class="st-hl">${escapeHtml(raw.slice(hit, hit + q.length))}</mark>`;
      pos = hit + q.length;
      hit = norm.indexOf(q, pos);
    }
    return out + escapeHtml(raw.slice(pos));
  }

  function stRenderAppsExplorer() {
    if (!stAppsList) return;
    const apps = stLastApps;
    if (apps.length === 0) {
      stAppsList.innerHTML = '<p class="muted-text">Aun no hay aplicaciones o pestanas registradas en este rango.</p>';
      if (stAppsCount) stAppsCount.textContent = '';
      return;
    }
    const q = stNormalize(stSearchQuery.trim());
    const filtering = Boolean(q) || stCatFilter !== 'ALL';
    const visible = [];
    apps.forEach((ap) => {
      if (stCatFilter !== 'ALL' && ap.category !== stCatFilter) return;
      const resources = ap.resources || [];
      if (!q) {
        visible.push({ ap, resources, forceOpen: false });
        return;
      }
      const appHit = stNormalize(ap.app_name).includes(q);
      const resHits = resources.filter((r) => stNormalize(r.resource_title).includes(q));
      if (appHit) visible.push({ ap, resources, forceOpen: resHits.length > 0 });
      else if (resHits.length > 0) visible.push({ ap, resources: resHits, forceOpen: true });
    });

    if (stAppsCount) {
      stAppsCount.textContent = filtering ? `${visible.length} de ${apps.length} apps` : `${apps.length} apps`;
    }
    if (visible.length === 0) {
      stAppsList.innerHTML = `<div class="st-apps-empty">
        <p class="muted-text">Sin coincidencias para el filtro actual.</p>
        <button type="button" class="st-res-link-btn js-st-clear-filters">Limpiar filtros</button>
      </div>`;
      return;
    }

    stAppsList.innerHTML = visible
      .map(({ ap, resources, forceOpen }, idx) => {
        const col = getCategoryColor(ap.category);
        const resRows = resources
          .map(
            (r) => `<div class="st-res-row">
                  <span class="st-res-title" title="${escapeHtml(r.resource_title)}">${stHighlight(r.resource_title, q)}</span>
                  <span class="st-res-meta">${r.switches_count || 0} apertura${r.switches_count === 1 ? '' : 's'} · <strong>${escapeHtml(formatAccumulatedTime(r.seconds || 0))}</strong></span>
                  <button type="button" class="st-res-link-btn js-st-open-captures" data-app="${escapeHtml(ap.app_name)}" data-res="${escapeHtml(r.resource_title)}">Ver capturas</button>
                </div>`
          )
          .join('');
        const isOpen = forceOpen || (!filtering && idx === 0);
        return `<details class="st-app-details" ${isOpen ? 'open' : ''}>
              <summary class="st-app-summary">
                <div class="st-app-left">
                  <svg class="st-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><polyline points="9 18 15 12 9 6"/></svg>
                  <span class="st-app-title" title="${escapeHtml(ap.app_name)}">${stHighlight(ap.app_name, q)}</span>
                  <span class="st-app-cat-pill" style="--st-cat-color:${col}">${escapeHtml(ap.category)}</span>
                </div>
                <div class="st-app-bar-wrap"><div class="st-app-bar-fill" style="width:${ap.percent}%; background:${col}"></div></div>
                <div class="st-app-right">
                  <span class="st-switches-pill">${ap.switches_count || 0} cambios</span>
                  <span class="st-app-dur">${escapeHtml(formatAccumulatedTime(ap.total_seconds || 0))}</span>
                </div>
              </summary>
              <div class="st-res-list">${resRows || '<span class="muted-text st-res-empty">Sin pestanas o documentos detallados.</span>'}</div>
            </details>`;
      })
      .join('');
  }

  function stSetCatFilter(cat) {
    stCatFilter = cat || 'ALL';
    if (stCatFilterChips) {
      stCatFilterChips.querySelectorAll('.st-chip').forEach((c) => {
        const on = c.dataset.cat === stCatFilter;
        c.classList.toggle('active', on);
        c.setAttribute('aria-pressed', on ? 'true' : 'false');
      });
    }
  }

  function renderScreenTimePanel(data) {
    const sum = data.summary || {};
    if (stKpiActive) stKpiActive.textContent = formatAccumulatedTime(sum.active_seconds || 0);
    if (stKpiIdle) stKpiIdle.textContent = formatAccumulatedTime(sum.idle_seconds || 0);
    if (stKpiSwitches) stKpiSwitches.textContent = String(sum.switches_count || 0);
    if (stSubtitleLabel) {
      const today = getTodayInTimezone(state.timezone);
      const d = data.date || stGetSelectedDate();
      const rel = d === today ? ' (hoy)' : d === stShiftIsoDate(today, -1) ? ' (ayer)' : '';
      stSubtitleLabel.textContent =
        data.period === '7d'
          ? `Acumulado de 7 dias (${data.start_date || ''} a ${data.end_date || data.date || ''})`
          : `Analisis detallado del ${d}${rel}`;
    }
    if (stKpiDelta) {
      const dp = Number(sum.delta_pct || 0);
      stKpiDelta.className = 'st-delta-badge ' + (dp > 0 ? 'st-delta-up' : dp < 0 ? 'st-delta-down' : '');
      const cmpLbl = data.period === '7d' ? 'vs 7d previos' : 'vs dia previo';
      stKpiDelta.textContent = sum.prev_active_seconds > 0 ? `${dp > 0 ? '+' : ''}${dp}% ${cmpLbl}` : 'Sin periodo previo';
    }

    stRenderHourly(Array.isArray(data.hourly) ? data.hourly : []);

    const cats = Array.isArray(data.categories) ? data.categories : [];
    stRenderFocusScore(cats);
    if (stCategoryBar && stCategoryLegend) {
      if (cats.length === 0) {
        stCategoryBar.innerHTML = '';
        stCategoryLegend.innerHTML = '<span class="muted-text">Sin actividad categorizada en este periodo.</span>';
      } else {
        stCategoryBar.innerHTML = cats
          .map((c) => `<div class="st-cat-seg" style="width:${c.percent}%; background:${getCategoryColor(c.category)}" title="${escapeHtml(c.category)}: ${formatAccumulatedTime(c.seconds)}"></div>`)
          .join('');
        stCategoryLegend.innerHTML = cats
          .map(
            (c) => `<div class="st-cat-item">
              <span class="st-cat-name"><i class="st-dot" style="background:${getCategoryColor(c.category)}"></i>${escapeHtml(c.category)}</span>
              <span class="st-cat-time">${escapeHtml(formatAccumulatedTime(c.seconds))}</span>
            </div>`
          )
          .join('');
      }
    }

    stLastApps = Array.isArray(data.apps) ? data.apps : [];
    stRenderTopCompare(stLastApps);
    stRenderAppsExplorer();
  }

  async function loadDailyMetrics() {
    loadScreenTimeAnalytics();
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
            <tr class="metrics-row-selectable ${stSelectedAgentId === m.agent_id ? 'metrics-row-selected' : ''}" data-row-agent="${escapeHtml(m.agent_id)}">
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

  function normalizeKeyboardLayoutName(rawLayout) {
    const val = (rawLayout || '').trim();
    if (!val || val === 'C' || val === 'POSIX' || val === 'Estándar') {
      return 'Español (Estándar)';
    }
    if (val === 'Latin American') return 'Español (Latinoamérica)';
    return val;
  }

  function renderKeyCapHtml(rawKeyName, mult) {
    const cleanName = String(rawKeyName || '').replace(/^\[|\]$/g, '').trim();
    const count = mult && mult > 1 ? mult : 1;
    const multHtml = count > 1 ? `<em class="kb-key-mult">×${count}</em>` : '';

    let cls = 'kb-key-mod';
    let label = cleanName;
    let title = `Tecla: ${cleanName}`;
    let svg = '';

    if (cleanName === 'Borrar' || cleanName === 'Supr') {
      cls = 'kb-key-delete';
      label = cleanName;
      title = cleanName === 'Borrar' ? 'Retroceso / Borrar carácter anterior (Backspace)' : 'Suprimir carácter (Delete)';
      svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z"/><line x1="18" y1="9" x2="12" y2="15"/><line x1="12" y1="9" x2="18" y2="15"/></svg>';
    } else if (cleanName === 'Enter') {
      cls = 'kb-key-action';
      label = 'Enter';
      title = 'Salto de línea / Confirmar (Enter)';
      svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="9 10 4 15 9 20"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>';
    } else if (cleanName === 'Tab') {
      cls = 'kb-key-action';
      label = 'Tab';
      title = 'Tabulador (Tab)';
      svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';
    } else if (cleanName === 'Espacio') {
      cls = 'kb-key-action';
      label = 'Espacio';
      title = 'Barra espaciadora';
    } else if (cleanName === 'RePág' || cleanName === 'AvPág') {
      cls = 'kb-key-nav';
      label = cleanName;
      title = cleanName === 'RePág' ? 'Retroceder Página (Page Up)' : 'Avanzar Página (Page Down)';
      svg = cleanName === 'RePág'
        ? '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="17 11 12 6 7 11"/><polyline points="17 18 12 13 7 18"/></svg>'
        : '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><polyline points="7 13 12 18 17 13"/><polyline points="7 6 12 11 17 6"/></svg>';
    } else if (cleanName.includes('Arriba') || cleanName.includes('Abajo') || cleanName.includes('Izquierda') || cleanName.includes('Derecha')) {
      cls = 'kb-key-nav';
      label = cleanName.replace(/^[↑↓←→]\s*/, '');
      title = `Cursor: ${label}`;
      if (cleanName.includes('Arriba')) {
        svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>';
      } else if (cleanName.includes('Abajo')) {
        svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="12" y1="5" x2="12" y2="19"/><polyline points="19 12 12 19 5 12"/></svg>';
      } else if (cleanName.includes('Izquierda')) {
        svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>';
      } else {
        svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>';
      }
    } else if (cleanName === 'Inicio' || cleanName === 'Fin' || cleanName === 'Esc' || cleanName === 'Insert') {
      cls = 'kb-key-nav';
      label = cleanName;
      title = `Tecla de navegación: ${cleanName}`;
    } else if (cleanName.includes('+')) {
      cls = 'kb-key-shortcut';
      const actionMap = {
        'CMD+C': 'Cmd + C (Copiar)',
        'CMD+V': 'Cmd + V (Pegar)',
        'CMD+X': 'Cmd + X (Cortar)',
        'CMD+Z': 'Cmd + Z (Deshacer)',
        'CTRL+C': 'Ctrl + C (Copiar)',
        'CTRL+V': 'Ctrl + V (Pegar)',
        'CTRL+X': 'Ctrl + X (Cortar)',
        'CTRL+Z': 'Ctrl + Z (Deshacer)',
      };
      const up = cleanName.toUpperCase();
      label = actionMap[up] || cleanName;
      title = `Atajo de teclado: ${label}`;
      svg = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z"/></svg>';
    }

    if ( count > 1 ) {
      title += ` (repetido ${count} veces)`;
    }

    return `<kbd class="kb-key ${cls}" title="${escapeHtml(title)}">${svg}<span>${escapeHtml(label)}</span>${multHtml}</kbd>`;
  }

  function analyzeKeyboardTranscript(rawText) {
    if (!rawText) {
      return { cleanText: '', sequenceHtml: '', hasSpecialKeys: false };
    }
    // 1. Limpiar el espacio artificial simple alrededor de [Token] generado por versiones previas del agente
    let norm = String(rawText).replace(/ ?(\[[A-Za-z0-9ÁÉÍÓÚáéíóúñÑ_↑↓←→\s]+\]) ?/g, '$1');
    // 2. Unificar atajos comunes [Cmd] c / [Ctrl] v
    norm = norm.replace(/\[(Cmd|Ctrl)\]\s*([cvxzaCVXZA])(?![a-zA-Záéíóúñ])/g, '[$1+$2]');

    // 3. Tokenizar en [Token], saltos de línea o texto normal
    const rawParts = norm.split(/(\[[A-Za-z0-9ÁÉÍÓÚáéíóúñÑ+_↑↓←→\s]+\]|\n)/g).filter(Boolean);

    const cleanChars = [];
    const seqItems = [];
    let hasSpecialKeys = false;

    for (let i = 0; i < rawParts.length; i++) {
      const part = rawParts[i];
      if (part === '\n') {
        cleanChars.push('\n');
        const prev = seqItems[seqItems.length - 1];
        if (!prev || prev.type !== 'key' || prev.key !== 'Enter') {
          hasSpecialKeys = true;
          seqItems.push({ type: 'key', key: 'Enter', count: 1 });
        }
      } else if (part.startsWith('[') && part.endsWith(']')) {
        const k = part.slice(1, -1).trim();
        // Ignorar modificadores redundantes (BloqMayús / Shift suelto) que ensucian cada mayúscula
        if (k === 'BloqMayús' || k === 'Shift') {
          continue;
        }
        if (k === 'Espacio') {
          cleanChars.push(' ');
          seqItems.push({ type: 'text', val: ' ' });
          continue;
        }
        hasSpecialKeys = true;
        if (k === 'Borrar') {
          cleanChars.pop();
        } else if (k === 'Enter') {
          cleanChars.push('\n');
        } else if (k === 'Tab') {
          cleanChars.push(' ');
        }
        const prev = seqItems[seqItems.length - 1];
        if (prev && prev.type === 'key' && prev.key === k) {
          prev.count += 1;
        } else {
          seqItems.push({ type: 'key', key: k, count: 1 });
        }
      } else {
        for (const ch of part) {
          cleanChars.push(ch);
        }
        seqItems.push({ type: 'text', val: part });
      }
    }

    const cleanText = cleanChars
      .join('')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n[ \t]+/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();

    const sequenceHtml = seqItems
      .map((item) => {
        if (item.type === 'key') {
          return renderKeyCapHtml(item.key, item.count);
        }
        return escapeHtml(item.val);
      })
      .join('');

    return {
      cleanText,
      sequenceHtml,
      hasSpecialKeys,
    };
  }

  function formatSpecialKeysHtml(text) {
    if (!text) return '';
    return analyzeKeyboardTranscript(text).sequenceHtml;
  }

  let evidenceNavList = [];
  let evidenceNavIdx = -1;
  let evidenceOnStepSync = null;
  let evidenceCurrentRawUrl = '';
  let evidenceIsHd = false;
  let evidenceInheritedLbl = '';

  function buildCaptureUrl(rawPath, isPreview = false) {
    if (!rawPath) return '';
    let u = rawPath;
    if (state.password) {
      const sep = u.includes('?') ? '&' : '?';
      u = `${u}${sep}admin_pass=${encodeURIComponent(state.password)}`;
    }
    if (isPreview) {
      const sep = u.includes('?') ? '&' : '?';
      u = `${u}${sep}preview=1`;
    }
    return u;
  }

  function syncEvidenceQualityUI() {
    if (!evidenceCurrentRawUrl) {
      if (evidenceHdBtn) evidenceHdBtn.classList.add('hidden');
      if (evidenceOriginBadge) evidenceOriginBadge.classList.add('hidden');
      return;
    }
    if (evidenceHdBtn) {
      evidenceHdBtn.classList.remove('hidden');
      evidenceHdBtn.classList.toggle('is-hd-on', evidenceIsHd);
    }
    if (evidenceHdLabel) {
      evidenceHdLabel.textContent = evidenceIsHd ? 'Alta Calidad (HD) activa' : 'Ver en Alta Calidad (HD)';
    }
    if (evidenceOriginBadge) {
      evidenceOriginBadge.classList.remove('hidden');
      evidenceOriginBadge.classList.toggle('is-inherited', Boolean(evidenceInheritedLbl));
      const baseTxt = evidenceInheritedLbl ? `Última captura previa · ${evidenceInheritedLbl}` : 'Captura del evento';
      evidenceOriginBadge.textContent = `${baseTxt} · ${evidenceIsHd ? 'Alta Calidad HD' : 'Vista previa ligera'}`;
    }
    if (evidenceImagePreview) {
      evidenceImagePreview.src = buildCaptureUrl(evidenceCurrentRawUrl, !evidenceIsHd);
    }
  }

  function logRowToEvidencePayload(row, chronoList) {
    if (!row) return null;
    let m = null;
    if (row.meta_json) {
      try { m = typeof row.meta_json === 'string' ? JSON.parse(row.meta_json) : row.meta_json; } catch (_) {}
    }
    let cap = row.evidence_path || (m && m.evidence_path) || '';
    let inheritedTime = '';
    if (!cap && Array.isArray(chronoList)) {
      const myIdx = chronoList.findIndex((x) => String(x.id) === String(row.id));
      const scanEnd = myIdx >= 0 ? myIdx - 1 : chronoList.length - 1;
      for (let i = scanEnd; i >= 0; i--) {
        const cand = chronoList[i];
        if (cand.agent_id !== row.agent_id) continue;
        let cm = null;
        if (cand.meta_json) {
          try { cm = typeof cand.meta_json === 'string' ? JSON.parse(cand.meta_json) : cand.meta_json; } catch (_) {}
        }
        const cCap = cand.evidence_path || (cm && cm.evidence_path) || '';
        if (cCap) {
          cap = cCap;
          inheritedTime = String(cand.timestamp || '').slice(11, 19);
          break;
        }
      }
    }
    return {
      category: row.category || 'WINDOW',
      url: cap,
      inheritedTime,
      alias: row.alias || 'Equipo',
      hostname: row.hostname || '',
      window: (m && m.active_window) || 'Escritorio',
      prevWindow: (m && m.previous_window) || '',
      time: row.timestamp || '',
      count: m && m.keystrokes_count !== undefined ? m.keystrokes_count : 0,
      duration: m && m.duration_seconds !== undefined ? m.duration_seconds : (row.duration_seconds || 0),
      layout: (m && m.keyboard_layout) || '',
      text: (m && m.text_content) || '',
      message: row.message || '',
    };
  }

  function stepEvidenceModal(dir) {
    if (!Array.isArray(evidenceNavList) || evidenceNavList.length === 0) return false;
    const nextIdx = evidenceNavIdx + dir;
    if (nextIdx < 0 || nextIdx >= evidenceNavList.length) return false;
    const targetRow = evidenceNavList[nextIdx];
    const payload = logRowToEvidencePayload(targetRow, evidenceNavList);
    if (!payload) return false;
    if (typeof evidenceOnStepSync === 'function') evidenceOnStepSync(targetRow);
    openEvidenceModal(payload, { list: evidenceNavList, index: nextIdx, onSync: evidenceOnStepSync });
    return true;
  }

  function openEvidenceModal(data, navOpts = null) {
    if (!evidenceModal || !data) return;
    if (navOpts && Array.isArray(navOpts.list) && navOpts.list.length > 0) {
      evidenceNavList = navOpts.list;
      evidenceNavIdx = Math.max(0, Math.min(navOpts.list.length - 1, Number(navOpts.index || 0)));
      evidenceOnStepSync = navOpts.onSync || null;
    } else {
      evidenceNavList = [];
      evidenceNavIdx = -1;
      evidenceOnStepSync = null;
    }
    const cat = String(data.category || 'KEYBOARD').toUpperCase();
    const isKeyboard = cat === 'KEYBOARD';
    if (evidenceModalTitle) {
      evidenceModalTitle.textContent = `Evidencia: ${data.alias} (${data.hostname})`;
    }
    if (evidenceModalSubtitle) {
      const catSubtitleMap = {
        KEYBOARD: 'Pausa de 15s tras redacción activa',
        WINDOW: 'Captura DVR automática al cambiar de ventana activa',
        IDLE: 'Captura DVR automática al entrar en estado de inactividad',
        RESUME: 'Captura DVR automática al retomar actividad en el equipo',
      };
      const subDesc = catSubtitleMap[cat] || 'Captura DVR de evento en bitácora';
      evidenceModalSubtitle.textContent = `Registrado el ${data.time} • ${subDesc}`;
    }

    if (evidenceMetaContainer) {
      if (isKeyboard) {
        const parsed = analyzeKeyboardTranscript(data.text || '');
        const cleanLayout = normalizeKeyboardLayoutName(data.layout);
        const cleanBlockHtml = parsed.cleanText
          ? `
            <div>
              <div class="kb-box-caption">Texto Final Redactado (Limpio)</div>
              <div class="kb-clean-box">${escapeHtml(parsed.cleanText)}</div>
            </div>
          `
          : '';
        const sequenceBlockHtml = parsed.sequenceHtml
          ? `
            <div>
              <div class="kb-box-caption">${parsed.hasSpecialKeys ? 'Secuencia de Pulsaciones y Teclas Especiales' : 'Transcripción'}</div>
              <div class="kb-raw-box">${parsed.sequenceHtml}</div>
            </div>
          `
          : '<div class="kb-raw-box muted-text">(Pulsaciones especiales sin texto imprimible)</div>';

        evidenceMetaContainer.innerHTML = `
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Ventana Activa</span>
            <span class="evidence-meta-val">${escapeHtml(data.window || 'Escritorio')}</span>
          </div>
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Pulsaciones</span>
            <span class="evidence-meta-val">${escapeHtml(String(data.count || 0))} teclas</span>
          </div>
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Duración</span>
            <span class="evidence-meta-val">${escapeHtml(String(data.duration || 0))}s</span>
          </div>
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Distribución / Idioma</span>
            <span class="evidence-meta-val">${escapeHtml(cleanLayout)}</span>
          </div>
          <div class="evidence-meta-item full-width">
            <div class="kb-transcript-stack">
              ${cleanBlockHtml}
              ${parsed.hasSpecialKeys || !parsed.cleanText ? sequenceBlockHtml : ''}
            </div>
          </div>
        `;
      } else {
        const catLabelMap = {
          WINDOW: 'Cambio de Ventana Activa',
          IDLE: 'Transición de Inactividad',
          RESUME: 'Regreso de Actividad',
          NETWORK: 'Evento de Red',
          AUDIT: 'Evento de Soporte',
        };
        const prevWin = (data.prevWindow || '').trim();
        evidenceMetaContainer.innerHTML = `
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Tipo de Evento DVR</span>
            <span class="evidence-meta-val">${escapeHtml(catLabelMap[cat] || cat)}</span>
          </div>
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Ventana Activa Capturada</span>
            <span class="evidence-meta-val">${escapeHtml(data.window || 'Escritorio')}</span>
          </div>
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">${prevWin ? 'Ventana Previa' : 'Duración Asociada'}</span>
            <span class="evidence-meta-val">${escapeHtml(prevWin ? prevWin : `${data.duration || 0}s`)}</span>
          </div>
          <div class="evidence-meta-item">
            <span class="evidence-meta-label">Permanencia / Tiempo</span>
            <span class="evidence-meta-val">${escapeHtml(String(data.duration || 0))}s</span>
          </div>
          <div class="evidence-meta-item full-width">
            <span class="evidence-meta-label">Detalle del Evento Registrado</span>
            <div class="kb-clean-box" style="margin-top:4px; font-weight:500;">${escapeHtml(data.message || '')}</div>
          </div>
        `;
      }
    }
    const hasNav = evidenceNavList.length > 1;
    if (evidencePosBadge) {
      evidencePosBadge.textContent = `Evento ${evidenceNavIdx + 1} / ${evidenceNavList.length}`;
      evidencePosBadge.classList.toggle('hidden', evidenceNavList.length === 0);
    }
    [evidencePrevBtn, evidenceStagePrev].forEach((b) => {
      if (!b) return;
      b.classList.toggle('hidden', !hasNav);
      b.disabled = evidenceNavIdx <= 0;
    });
    [evidenceNextBtn, evidenceStageNext].forEach((b) => {
      if (!b) return;
      b.classList.toggle('hidden', !hasNav);
      b.disabled = evidenceNavIdx >= evidenceNavList.length - 1;
    });

    evidenceCurrentRawUrl = data.url || '';
    evidenceInheritedLbl = data.inheritedTime || '';
    evidenceIsHd = false;
    const fullImgUrl = buildCaptureUrl(evidenceCurrentRawUrl, false);
    if (evidenceImagePreview && evidenceNoImage) {
      if (evidenceCurrentRawUrl) {
        evidenceImagePreview.classList.remove('hidden');
        evidenceNoImage.classList.add('hidden');
        evidenceImagePreview.alt = 'Cargando captura de pantalla...';
        evidenceImagePreview.onerror = function () {
          this.alt = 'No se pudo cargar la imagen de evidencia visual.';
        };
      } else {
        evidenceImagePreview.src = '';
        evidenceImagePreview.classList.add('hidden');
        evidenceNoImage.classList.remove('hidden');
      }
    }
    syncEvidenceQualityUI();
    if (evidenceDownloadLink) {
      evidenceDownloadLink.classList.toggle('hidden', !fullImgUrl);
      evidenceDownloadLink.href = fullImgUrl || '#';
      const cleanAlias = (data.alias || 'equipo').replace(/[^a-zA-Z0-9_-]/g, '_');
      const isWebp = String(data.url || '').toLowerCase().includes('.webp');
      const fileExt = isWebp ? 'webp' : 'jpg';
      evidenceDownloadLink.download = `evidencia_${cleanAlias}_${Date.now()}.${fileExt}`;
      const dlSpan = evidenceDownloadLink.querySelector('span');
      if (dlSpan) {
        dlSpan.textContent = isWebp ? 'Descargar Evidencia WebP' : 'Descargar Evidencia JPG';
      }
    }
    evidenceModal.classList.remove('hidden');
  }

  function closeEvidenceModal() {
    if (evidenceModal) {
      evidenceModal.classList.add('hidden');
      if (evidenceImagePreview) {
        evidenceImagePreview.src = '';
      }
    }
  }

  const logCategoryChips = document.getElementById('log-category-chips');

  function syncLogCategoryChips() {
    if (!logCategoryChips || !logCategoryFilter) return;
    const cur = logCategoryFilter.value || 'ALL';
    logCategoryChips.querySelectorAll('.log-cat-chip').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.cat === cur);
    });
  }

  function getLogCatIconSvg(cat) {
    switch (cat) {
      case 'KEYBOARD':
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M7 16h10"/></svg>';
      case 'IDLE':
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 15"/></svg>';
      case 'RESUME':
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>';
      case 'AUDIT':
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>';
      case 'NETWORK':
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12.01" y2="20"/></svg>';
      default:
        return '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>';
    }
  }

  function buildLogThumbHtml(row, cat, activeWin, prevWin, durSecs, countVal, layoutVal, textVal, evidencePath) {
    if (!evidencePath) return '';
    const authedSrc = buildCaptureUrl(evidencePath, true);
    return `
      <button type="button" class="log-thumb-card js-open-evidence" title="Ampliar captura de pantalla"
        data-log-id="${escapeHtml(String(row.id || ''))}"
        data-category="${escapeHtml(cat)}"
        data-evidence-url="${escapeHtml(evidencePath)}"
        data-alias="${escapeHtml(row.alias)}"
        data-hostname="${escapeHtml(row.hostname)}"
        data-window="${escapeHtml(activeWin)}"
        data-prev-window="${escapeHtml(prevWin || '')}"
        data-time="${escapeHtml(row.timestamp)}"
        data-count="${escapeHtml(String(countVal || 0))}"
        data-duration="${escapeHtml(String(durSecs || 0))}"
        data-layout="${escapeHtml(layoutVal || '')}"
        data-text="${escapeHtml(textVal || '')}"
        data-message="${escapeHtml(row.message || '')}">
        <img src="${escapeHtml(authedSrc)}" alt="Captura" loading="lazy" />
        <span class="log-thumb-badge">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3"><polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" y1="3" x2="14" y2="10"/><line x1="3" y1="21" x2="10" y2="14"/></svg>
          <span>Captura</span>
        </span>
      </button>`;
  }

  function renderLogsTable() {
    syncLogCategoryChips();
    if (logsCounterBadge) {
      const count = state.logsCache ? state.logsCache.length : 0;
      logsCounterBadge.textContent = `${count} evento${count !== 1 ? 's' : ''}`;
    }

    if (!state.logsCache || state.logsCache.length === 0) {
      logsTbody.innerHTML = '<div class="logs-empty-feed">No se encontraron eventos con los filtros seleccionados.</div>';
      return;
    }

    const categoryLabels = {
      KEYBOARD: 'Teclado',
      IDLE: 'Inactividad',
      RESUME: 'Regreso',
      WINDOW: 'App / Pestaña',
      NETWORK: 'Conexión',
      AUDIT: 'Soporte',
    };

    let itemsToRender = [];
    if (state.groupConsecutiveLogs) {
      for (let i = 0; i < state.logsCache.length; i++) {
        const row = state.logsCache[i];
        const prev = itemsToRender[itemsToRender.length - 1];
        if (prev && prev.agent_id === row.agent_id && prev.category === row.category && prev.message === row.message) {
          prev.repeatCount = (prev.repeatCount || 1) + 1;
          prev.firstTimestamp = row.timestamp;
        } else {
          itemsToRender.push({ ...row, repeatCount: 1, firstTimestamp: row.timestamp });
        }
      }
    } else {
      itemsToRender = state.logsCache.map((r) => ({ ...r, repeatCount: 1, firstTimestamp: r.timestamp }));
    }

    logsTbody.innerHTML = itemsToRender
      .map((row) => {
        const cat = row.category || 'WINDOW';
        const catText = categoryLabels[cat] || cat;
        let meta = null;
        if (row.meta_json) {
          try { meta = typeof row.meta_json === 'string' ? JSON.parse(row.meta_json) : row.meta_json; } catch (_) {}
        }
        const evidencePath = row.evidence_path || (meta && meta.evidence_path) || '';
        const activeWin = (meta && meta.active_window) || 'Escritorio';
        const prevWin = (meta && meta.previous_window) || '';
        const durSecs = meta && meta.duration_seconds !== undefined ? meta.duration_seconds : (row.duration_seconds || 0);
        const rawTs = String(row.timestamp || '');
        const tsParts = rawTs.split(' ');
        const timePart = tsParts.length > 1 ? tsParts.slice(1).join(' ') : rawTs;
        const datePart = tsParts.length > 1 ? tsParts[0] : '';

        let bodyHtml = '';
        let thumbHtml = '';

        if (cat === 'KEYBOARD') {
          const keystrokesCount = meta && meta.keystrokes_count !== undefined ? meta.keystrokes_count : durSecs;
          const layout = normalizeKeyboardLayoutName((meta && meta.keyboard_layout) || 'Estándar');
          const textContent = (meta && meta.text_content) || '';
          const parsed = analyzeKeyboardTranscript(textContent);

          let kbMain = '<span class="muted-text">Pulsaciones de navegacion / teclas especiales</span>';
          if (parsed.cleanText && parsed.hasSpecialKeys) {
            kbMain = `<div class="log-kb-clean-text">${escapeHtml(parsed.cleanText)}</div>
              <details class="log-kb-details"><summary>Ver secuencia completa de teclas (${escapeHtml(String(keystrokesCount))})</summary><div class="log-kb-seq">${parsed.sequenceHtml}</div></details>`;
          } else if (parsed.cleanText) {
            kbMain = `<div class="log-kb-clean-text">${escapeHtml(parsed.cleanText)}</div>`;
          } else if (parsed.sequenceHtml) {
            kbMain = `<div class="log-kb-seq">${parsed.sequenceHtml}</div>`;
          }

          bodyHtml = `
            <div class="log-kb-entry">
              <div class="log-kb-header">
                <span class="log-kb-win-tag"><strong>${escapeHtml(activeWin)}</strong></span>
                <span class="log-kb-pill">${escapeHtml(String(keystrokesCount))} teclas</span>
                <span class="log-kb-pill">${durSecs}s</span>
                <span class="log-kb-pill layout-pill">${escapeHtml(layout)}</span>
              </div>
              <div class="log-kb-quote">${kbMain}</div>
              ${buildCorrelationHtml(meta)}
            </div>`;
          thumbHtml = buildLogThumbHtml(row, 'KEYBOARD', activeWin, prevWin, durSecs, keystrokesCount, layout, textContent, evidencePath);
        } else {
          const winChipsHtml =
            cat === 'WINDOW' && meta && meta.app_name
              ? `<div class="win-log-chips">
                  <span class="win-chip-app">${escapeHtml(meta.app_name)}</span>
                  ${meta.resource_title && meta.resource_title !== 'Ventana principal' ? `<span class="win-chip-tab" title="${escapeHtml(meta.resource_title)}">${escapeHtml(meta.resource_title)}</span>` : ''}
                  ${meta.category_tag ? `<span class="win-chip-cat">${escapeHtml(meta.category_tag)}</span>` : ''}
                </div>`
              : '';
          bodyHtml = `
            <div class="log-normal-body">
              ${winChipsHtml}
              <div class="log-msg-line">${formatSpecialKeysHtml(row.message)}</div>
              ${cat === 'NETWORK' ? buildCorrelationHtml(meta) : ''}
            </div>`;
          thumbHtml = buildLogThumbHtml(row, cat, activeWin, prevWin, durSecs, 0, '', '', evidencePath);
        }

        const repeatHtml = row.repeatCount > 1
          ? `<span class="log-repeat-pill" title="Repetido ${row.repeatCount} veces consecutivas">x${row.repeatCount}</span>`
          : '';
        const durBadgeHtml = cat !== 'KEYBOARD' && durSecs > 0
          ? `<span class="log-dur-pill">${escapeHtml(formatAccumulatedTime(durSecs))}</span>`
          : '';

        return `
          <article class="log-feed-item">
            <div class="log-rail">
              <span class="log-node log-node-${escapeHtml(cat)}">${getLogCatIconSvg(cat)}</span>
            </div>
            <div class="log-card-box" data-log-id="${escapeHtml(String(row.id || ''))}" title="Clic para inspeccionar evento y captura">
              <div class="log-meta-top">
                <div class="log-meta-left">
                  <button type="button" class="log-device-chip js-log-filter-device" data-agent-id="${escapeHtml(row.agent_id || '')}" title="Filtrar bitacora de ${escapeHtml(row.alias)}">
                    <strong>${escapeHtml(row.alias)}</strong>
                    <span>· ${escapeHtml(row.hostname)}</span>
                  </button>
                  <span class="cat-badge cat-${escapeHtml(cat)}">${escapeHtml(catText)}</span>
                  ${durBadgeHtml}
                  ${repeatHtml}
                </div>
                <div class="log-meta-time">
                  <strong>${escapeHtml(timePart)}</strong>
                  ${datePart ? `<span>${escapeHtml(datePart)}</span>` : ''}
                </div>
              </div>
              <div class="log-content-row">
                <div class="log-content-main">${bodyHtml}</div>
                ${thumbHtml}
              </div>
            </div>
          </article>`;
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
      fetchDvrDayFromServer();
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
      network: viewNetwork,
      settings: viewSettings,
    };
    const tabs = {
      wall: tabWall,
      viewer: tabViewer,
      activity: tabActivity,
      logs: tabLogs,
      network: tabNetwork,
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
    } else if (target === 'network') {
      loadNetworkDashboard();
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

    const vTermBtn = document.getElementById('viewer-terminal-btn');
    if (isSub && !user.can_control) {
      if (toggleControlBtn) toggleControlBtn.classList.add('hidden');
      if (pttBtn) pttBtn.classList.add('hidden');
      if (openClipboardBtn) openClipboardBtn.classList.add('hidden');
      if (openFileBtn) openFileBtn.classList.add('hidden');
      if (viewerUnlinkBtn) viewerUnlinkBtn.classList.add('hidden');
      if (vTermBtn) vTermBtn.classList.add('hidden');
    } else {
      if (toggleControlBtn) toggleControlBtn.classList.remove('hidden');
      if (pttBtn) pttBtn.classList.remove('hidden');
      if (openClipboardBtn) openClipboardBtn.classList.remove('hidden');
      if (openFileBtn) openFileBtn.classList.remove('hidden');
      if (viewerUnlinkBtn) viewerUnlinkBtn.classList.toggle('hidden', isSub);
      if (vTermBtn) vTermBtn.classList.remove('hidden');
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
    try {
      document.cookie = 'ra_admin_pass=; path=/; max-age=0; SameSite=Lax';
    } catch (_) {}
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
        if (stDatePicker) stDatePicker.value = metricsDateInput ? metricsDateInput.value : getTodayInTimezone(state.timezone);
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
        if (state.currentView === 'network') netOnLiveTelemetry(msg);
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
        if (msg.log.category === 'NETWORK') netOnNetworkLog();
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
            pushLiveLogToDvr(l);
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

      if (msg.type === 'shell_ready') {
        if (rshShellStatus && msg.agent_id === rshActiveAgentId) {
          rshShellStatus.textContent = `Conectado · ${msg.shell_label || 'Shell activo'} · Invisible en pantalla remota`;
        }
        return;
      }

      if (msg.type === 'shell_output') {
        if (rshOutput && msg.agent_id === rshActiveAgentId) {
          appendTerminalOutput(msg.data || '');
          if (msg.closed && rshShellStatus) {
            rshShellStatus.textContent = 'Sesion de consola finalizada';
          }
        }
        return;
      }

      if (msg.type === 'fs_list_res' || msg.type === 'fs_download_res' || msg.type === 'fs_op_res') {
        handleFinderWsMessage(msg);
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
      try {
        document.cookie = `ra_admin_pass=${encodeURIComponent(state.password)}; path=/; max-age=2592000; SameSite=Lax`;
      } catch (_) {}
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
    const fndBtn = e.target.closest('.js-open-finder');
    if (fndBtn) {
      e.stopPropagation();
      openFinderExplorer(fndBtn.dataset.agentId);
      return;
    }
    const termBtn = e.target.closest('.js-open-terminal');
    if (termBtn) {
      e.stopPropagation();
      openReverseTerminal(termBtn.dataset.agentId);
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
      return;
    }
    const rowEl = e.target.closest('tr[data-row-agent]');
    if (rowEl && stDeviceSelect) {
      const nextId = rowEl.dataset.rowAgent || 'ALL';
      stSelectedAgentId = stSelectedAgentId === nextId ? 'ALL' : nextId;
      stDeviceSelect.value = stSelectedAgentId;
      loadDailyMetrics();
    }
  });

  if (stDeviceSelect) {
    stDeviceSelect.addEventListener('change', () => {
      stSelectedAgentId = stDeviceSelect.value || 'ALL';
      loadDailyMetrics();
    });
  }
  stPeriodBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.period || 'day';
      const today = getTodayInTimezone(state.timezone);
      if (key === '7d') {
        stCurrentPeriod = '7d';
        loadScreenTimeAnalytics();
        return;
      }
      stCurrentPeriod = 'day';
      const target = key === 'yesterday' ? stShiftIsoDate(today, -1) : today;
      const changed = stGetSelectedDate() !== target;
      stSetSelectedDate(target);
      // Si cambia la fecha se recarga tambien la tabla de Metricas Diarias (fecha compartida).
      if (changed) loadDailyMetrics();
      else loadScreenTimeAnalytics();
    });
  });
  if (stDatePicker) {
    stDatePicker.value = stGetSelectedDate();
    stDatePicker.addEventListener('change', () => {
      if (!stDatePicker.value) return;
      stSetSelectedDate(stDatePicker.value);
      loadDailyMetrics();
    });
  }
  if (stRefreshBtn) {
    stRefreshBtn.addEventListener('click', () => loadDailyMetrics());
  }
  if (stHourlyChart) {
    stHourlyChart.addEventListener('mouseover', (e) => {
      const col = e.target.closest('.st-hour-col');
      if (col) stShowHourTooltip(col);
    });
    stHourlyChart.addEventListener('mouseleave', stHideHourTooltip);
    stHourlyChart.addEventListener('focusin', (e) => {
      const col = e.target.closest('.st-hour-col');
      if (col) stShowHourTooltip(col);
    });
    stHourlyChart.addEventListener('focusout', stHideHourTooltip);
  }
  if (stAppSearch) {
    stAppSearch.addEventListener('input', () => {
      stSearchQuery = stAppSearch.value || '';
      stRenderAppsExplorer();
    });
    stAppSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && stAppSearch.value) {
        stAppSearch.value = '';
        stSearchQuery = '';
        stRenderAppsExplorer();
      }
    });
  }
  if (stCatFilterChips) {
    stCatFilterChips.addEventListener('click', (e) => {
      const chip = e.target.closest('.st-chip');
      if (!chip) return;
      stSetCatFilter(chip.dataset.cat);
      stRenderAppsExplorer();
    });
  }
  [stTopWork, stTopDistractions].forEach((box) => {
    if (!box) return;
    box.addEventListener('click', (e) => {
      const item = e.target.closest('.js-st-top-item');
      if (!item || !stAppSearch) return;
      stAppSearch.value = item.dataset.app || '';
      stSearchQuery = stAppSearch.value;
      stSetCatFilter('ALL');
      stRenderAppsExplorer();
      stAppSearch.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  });
  if (stAppsList) {
    stAppsList.addEventListener('click', (e) => {
      if (e.target.closest('.js-st-clear-filters')) {
        if (stAppSearch) stAppSearch.value = '';
        stSearchQuery = '';
        stSetCatFilter('ALL');
        stRenderAppsExplorer();
        return;
      }
      const capBtn = e.target.closest('.js-st-open-captures');
      if (!capBtn) return;
      e.preventDefault();
      e.stopPropagation();
      populateLogDeviceSelector();
      if (logDeviceFilter) logDeviceFilter.value = stSelectedAgentId || 'ALL';
      if (logCategoryFilter) logCategoryFilter.value = 'WINDOW';
      const resT = capBtn.dataset.res || '';
      const appN = capBtn.dataset.app || '';
      if (logSearchInput) logSearchInput.value = resT && resT !== 'Ventana principal' ? resT.slice(0, 38) : appN;
      switchView('logs');
      fetchLogsFromServer();
    });
  }

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

  // ===================== Vista: Tráfico de Red (estilo ntopng) =====================
  const netDateInput = document.getElementById('net-date-input');
  const netDeviceFilter = document.getElementById('net-device-filter');
  const netCategoryFilter = document.getElementById('net-category-filter');
  const netDirectionFilter = document.getElementById('net-direction-filter');
  const netFlowSortSelect = document.getElementById('net-flow-sort-select');
  const netSearchInput = document.getElementById('net-search-input');
  const refreshNetBtn = document.getElementById('refresh-net-btn');
  const exportNetCsvBtn = document.getElementById('export-net-csv-btn');
  const netClientsTbody = document.getElementById('net-clients-tbody');
  const netDomainsTbody = document.getElementById('net-domains-tbody');
  const netShowSysToggle = document.getElementById('net-show-sys-toggle');
  const netCounterBadge = document.getElementById('net-counter-badge');
  const netAutoToggle = document.getElementById('net-auto-toggle');
  const netLiveInd = document.querySelector('.ntop-live-ind');
  const netCanvas = document.getElementById('net-traffic-canvas');

  // Rediseño ntopng: Subnavegación, hosts grid, drawer y controles avanzados
  const netSubtabBtns = document.querySelectorAll('.ntop-subtab-btn');
  const netSubviewOverview = document.getElementById('net-subview-overview');
  const netSubviewHosts = document.getElementById('net-subview-hosts');
  const netSubviewFlows = document.getElementById('net-subview-flows');
  const netActiveHostPill = document.getElementById('net-active-host-pill');
  const netActiveHostName = document.getElementById('net-active-host-name');
  const netClearHostBtn = document.getElementById('net-clear-host-btn');
  const netChartTooltip = document.getElementById('net-chart-tooltip');
  const netChartRangeBtns = document.querySelectorAll('.net-range-btn');
  const netTopTalkersLeaderboard = document.getElementById('net-top-talkers-leaderboard');
  const netGotoHostsBtn = document.getElementById('net-goto-hosts-btn');
  const netHostSearchInput = document.getElementById('net-host-search-input');
  const netHostSortSelect = document.getElementById('net-host-sort-select');
  const netHostViewCardsBtn = document.getElementById('net-host-view-cards');
  const netHostViewTableBtn = document.getElementById('net-host-view-table');
  const netHostsGrid = document.getElementById('net-hosts-grid');
  const netHostsTableWrap = document.getElementById('net-hosts-table-wrap');
  const netCategoryChipBtns = document.querySelectorAll('.net-chip-btn');
  const netCategoryChipsBar = document.getElementById('net-category-chips');
  const netDirectionPillBtns = document.querySelectorAll('#net-direction-pills .net-ft-seg-btn');
  const netSearchClearBtn = document.getElementById('net-search-clear-btn');
  const netClearFiltersBtn = document.getElementById('net-clear-filters-btn');
  const netDrawer = document.getElementById('net-drawer');
  const netDrawerBackdrop = document.getElementById('net-drawer-backdrop');
  const netDrawerTitle = document.getElementById('net-drawer-title');
  const netDrawerBadge = document.getElementById('net-drawer-badge');
  const netDrawerBody = document.getElementById('net-drawer-body');
  const netDrawerClose = document.getElementById('net-drawer-close');

  const NET_TICK_MS = 4000;
  const NET_HISTORY_MAX = 150; // ~10 min a 4s
  const NET_CATEGORY_LABELS = {
    STREAMING: 'Streaming / Video',
    SOCIAL: 'Redes Sociales',
    MESSAGING: 'Mensajería',
    AI: 'Inteligencia Artificial',
    GAMING: 'Videojuegos',
    SHOPPING: 'Compras / E-commerce',
    CLOUD: 'Almacenamiento Cloud',
    REMOTE: 'Acceso Remoto',
    WORK: 'Desarrollo / Trabajo',
    SYSTEM: 'Sistema / Actualizaciones',
    OTHER: 'Otros',
  };
  const NET_CATEGORY_COLORS = {
    STREAMING: '#e11d48', SOCIAL: '#f97316', MESSAGING: '#06b6d4', AI: '#8b5cf6',
    GAMING: '#eab308', SHOPPING: '#ec4899',
    CLOUD: '#6366f1', REMOTE: '#d946ef', WORK: '#16a34a', SYSTEM: '#64748b', OTHER: '#94a3b8',
  };
  const NET_PALETTE = ['#e11d48', '#06b6d4', '#16a34a', '#8b5cf6', '#f97316', '#94a3b8'];

  const netState = {
    summary: null,
    domains: [],
    history: [],
    auto: true,
    loading: false,
    liveDomains: new Map(),
    refreshTimer: null,
    lastRender: 0,
    expandedFlowKeys: new Set(),
    subview: 'overview',
    chartRange: '10m',
    hostSearchQuery: '',
    hostSortBy: 'bytes',
    hostViewMode: 'cards',
    hostHistory: new Map(),
  };

  // Tiempo real por WebSocket: debounce de render, sincronizacion por log y respaldo HTTP relajado
  const NET_WS_RENDER_DEBOUNCE_MS = 800;
  const NET_LOG_SYNC_DEBOUNCE_MS = 3000;
  const NET_BACKUP_SYNC_MS = 45000;
  let netWsRenderTimer = null;
  let netLastSyncAt = 0;

  function formatBytes(bytes) {
    const n = Math.max(0, Number(bytes) || 0);
    if (n < 1024) return `${Math.round(n)} B`;
    const units = ['KB', 'MB', 'GB', 'TB'];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < units.length - 1) {
      v /= 1024;
      i += 1;
    }
    return `${v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)} ${units[i]}`;
  }

  // Entrada en kilobits por segundo
  function formatBitrate(kbps) {
    const v = Math.max(0, Number(kbps) || 0);
    if (v < 1000) return `${v >= 100 ? Math.round(v) : v.toFixed(1)} Kbps`;
    if (v < 1000000) return `${(v / 1000).toFixed(v >= 100000 ? 0 : 1)} Mbps`;
    return `${(v / 1000000).toFixed(2)} Gbps`;
  }

  function netTodayStr() {
    const d = new Date();
    const pad = (x) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  }

  function netBaseParams() {
    const params = new URLSearchParams({ date: (netDateInput && netDateInput.value) || netTodayStr() });
    if (state.tenantFilter && state.tenantFilter !== 'ALL') params.set('tenant_id', state.tenantFilter);
    return params;
  }

  function netFilterParams() {
    const params = netBaseParams();
    params.set('agent_id', (netDeviceFilter && netDeviceFilter.value) || 'ALL');
    params.set('category', (netCategoryFilter && netCategoryFilter.value) || 'ALL');
    params.set('q', netSearchInput ? netSearchInput.value.trim() : '');
    return params;
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function netSelectedHost() {
    return (netDeviceFilter && netDeviceFilter.value) || 'ALL';
  }

  // ---- Velocidad en vivo agregada (de los agentes conectados por WebSocket) ----
  function netLiveTotals() {
    const host = netSelectedHost();
    let rx = 0;
    let tx = 0;
    state.agents.forEach((a) => {
      if (host !== 'ALL' && a.agent_id !== host) return;
      if (state.tenantFilter !== 'ALL' && a.tenant_id && a.tenant_id !== state.tenantFilter) return;
      rx += Number(a.rx_kbps) || 0;
      tx += Number(a.tx_kbps) || 0;
    });
    return { rx, tx };
  }

  function netUpdateLiveIndicators() {
    const live = netLiveTotals();
    setText('net-live-rx', formatBitrate(live.rx));
    setText('net-live-tx', formatBitrate(live.tx));
    const sub = document.getElementById('net-kpi-rx-sub');
    if (sub) sub.textContent = `↓ ${formatBitrate(live.rx)}`;
  }

  function netPushSample() {
    const live = netLiveTotals();
    const now = Date.now();
    netState.history.push({ t: now, rx: live.rx, tx: live.tx });
    if (netState.history.length > NET_HISTORY_MAX) netState.history.shift();

    // Historial para sparklines por equipo (últimas 25 muestras)
    state.agents.forEach((ag, id) => {
      if (!netState.hostHistory.has(id)) netState.hostHistory.set(id, []);
      const hList = netState.hostHistory.get(id);
      hList.push({ t: now, rx: Number(ag.rx_kbps) || 0, tx: Number(ag.tx_kbps) || 0 });
      if (hList.length > 25) hList.shift();
    });
  }

  function drawSparkline(canvas, samples) {
    if (!canvas) return;
    const w = canvas.clientWidth || 280;
    const h = canvas.clientHeight || 38;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
    }
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    if (!samples || samples.length < 2) {
      ctx.fillStyle = '#94a3b8';
      ctx.font = '10px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText('Sin muestras recientes', w / 2, h / 2);
      return;
    }
    const max = Math.max(10, ...samples.map((s) => Math.max(s.rx || 0, s.tx || 0)));
    const step = w / (samples.length - 1);

    // RX fill & line (cyan)
    ctx.beginPath();
    ctx.moveTo(0, h);
    samples.forEach((s, i) => {
      const y = h - ((s.rx || 0) / max) * (h - 4);
      ctx.lineTo(i * step, y);
    });
    ctx.lineTo(w, h);
    ctx.closePath();
    ctx.fillStyle = 'rgba(2, 132, 199, 0.12)';
    ctx.fill();

    ctx.beginPath();
    samples.forEach((s, i) => {
      const y = h - ((s.rx || 0) / max) * (h - 4);
      if (i === 0) ctx.moveTo(0, y);
      else ctx.lineTo(i * step, y);
    });
    ctx.strokeStyle = '#0284c7';
    ctx.lineWidth = 1.5;
    ctx.stroke();

    // TX line (orange)
    ctx.beginPath();
    samples.forEach((s, i) => {
      const y = h - ((s.tx || 0) / max) * (h - 4);
      if (i === 0) ctx.moveTo(0, y);
      else ctx.lineTo(i * step, y);
    });
    ctx.strokeStyle = '#ea580c';
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  // ---- Gráfica de área bipolar (Canvas nativo) ----
  function netNiceMax(v) {
    if (v <= 10) return 10;
    const pow = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / pow;
    const step = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return step * pow;
  }

  function drawTrafficChart() {
    if (!netCanvas) return;
    const w = netCanvas.clientWidth;
    const h = netCanvas.clientHeight;
    if (!w || !h) return;
    const dpr = window.devicePixelRatio || 1;
    if (netCanvas.width !== Math.round(w * dpr) || netCanvas.height !== Math.round(h * dpr)) {
      netCanvas.width = Math.round(w * dpr);
      netCanvas.height = Math.round(h * dpr);
    }
    const ctx = netCanvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const padL = 64;
    const padR = 12;
    const padT = 10;
    const padB = 24;
    const cw = w - padL - padR;
    const ch = h - padT - padB;
    const midY = padT + ch / 2;
    const hist = netState.history;
    const peak = hist.reduce((m, p) => Math.max(m, p.rx, p.tx), 0);
    const maxV = netNiceMax(peak * 1.1);

    ctx.font = '10.5px system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'right';
    ctx.lineWidth = 1;

    // Rejilla y escala Y (autoadaptativa Kbps/Mbps)
    [-1, -0.5, 0, 0.5, 1].forEach((f) => {
      const y = midY - f * (ch / 2);
      ctx.strokeStyle = f === 0 ? '#8fa3b8' : '#e2e8f0';
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillStyle = '#7b8ea3';
      ctx.fillText(f === 0 ? '0 bps' : `${f < 0 ? '-' : ''}${formatBitrate(Math.abs(f) * maxV)}`, padL - 6, y);
    });

    if (hist.length < 2) {
      ctx.fillStyle = '#7b8ea3';
      ctx.textAlign = 'center';
      ctx.fillText('Recopilando muestras en vivo…', padL + cw / 2, midY - 14);
      return;
    }

    const tMax = hist[hist.length - 1].t;
    const tMin = Math.min(hist[0].t, tMax - NET_TICK_MS * 30);
    const xOf = (t) => padL + ((t - tMin) / (tMax - tMin)) * cw;
    const yOf = (v, dir) => midY - dir * (Math.min(v, maxV) / maxV) * (ch / 2);

    const drawArea = (key, dir, stroke, fillTop) => {
      const grad = ctx.createLinearGradient(0, midY, 0, midY - dir * (ch / 2));
      grad.addColorStop(0, fillTop.replace('ALPHA', '0.05'));
      grad.addColorStop(1, fillTop.replace('ALPHA', '0.55'));
      ctx.beginPath();
      ctx.moveTo(xOf(hist[0].t), midY);
      hist.forEach((p) => ctx.lineTo(xOf(p.t), yOf(p[key], dir)));
      ctx.lineTo(xOf(tMax), midY);
      ctx.closePath();
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.beginPath();
      hist.forEach((p, i) => {
        const x = xOf(p.t);
        const y = yOf(p[key], dir);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 1.6;
      ctx.stroke();
    };
    drawArea('rx', 1, '#16a34a', 'rgba(22,163,74,ALPHA)');
    drawArea('tx', -1, '#f97316', 'rgba(249,115,22,ALPHA)');

    // Marcas de tiempo en eje X
    ctx.fillStyle = '#7b8ea3';
    ctx.textBaseline = 'top';
    const ticks = Math.max(2, Math.min(6, Math.floor(cw / 110)));
    for (let i = 0; i <= ticks; i += 1) {
      const t = tMin + ((tMax - tMin) * i) / ticks;
      ctx.textAlign = i === 0 ? 'left' : i === ticks ? 'right' : 'center';
      const d = new Date(t);
      const label = d.toLocaleTimeString('es-MX', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
      ctx.fillText(label, padL + (cw * i) / ticks, h - padB + 6);
    }
  }

  // ---- Donas SVG ----
  function renderDonut(containerId, items, totalLabel) {
    const box = document.getElementById(containerId);
    if (!box) return;
    const total = items.reduce((s, it) => s + it.value, 0);
    if (!total) {
      box.innerHTML = '<div class="ntop-empty">Sin datos</div>';
      return;
    }
    let offset = 0;
    const circles = items.map((it) => {
      const pct = (it.value / total) * 100;
      const c = `<circle cx="21" cy="21" r="15.9155" fill="none" stroke="${it.color}" stroke-width="6" stroke-dasharray="${pct.toFixed(3)} ${(100 - pct).toFixed(3)}" stroke-dashoffset="${(25 - offset).toFixed(3)}"><title>${escapeHtml(it.label)}: ${pct.toFixed(1)}%</title></circle>`;
      offset += pct;
      return c;
    }).join('');
    const legend = items.map((it) => {
      const pct = (it.value / total) * 100;
      return `<li><span><i class="dot" style="background:${it.color}"></i>${escapeHtml(it.label)}</span><b>${pct.toFixed(pct >= 10 ? 0 : 1)}%</b></li>`;
    }).join('');
    box.innerHTML = `<svg class="ntop-donut-svg" viewBox="0 0 42 42" role="img" aria-label="${escapeHtml(totalLabel)}">
      <circle cx="21" cy="21" r="15.9155" fill="none" stroke="#e3eaf2" stroke-width="6"></circle>${circles}
      <text x="21" y="22.5" text-anchor="middle" font-size="5.2" font-weight="700">${escapeHtml(totalLabel)}</text></svg>
      <ul class="ntop-legend">${legend}</ul>`;
  }

  function netWeightedGroups(rows, keyFn) {
    const useBytes = rows.some((r) => (r.estimated_bytes || 0) > 0);
    const map = new Map();
    rows.forEach((r) => {
      const k = keyFn(r);
      const w = useBytes ? (r.estimated_bytes || 0) : (r.hit_count || 0);
      map.set(k, (map.get(k) || 0) + w);
    });
    return { groups: [...map.entries()].map(([k, v]) => ({ key: k, value: v })).filter((g) => g.value > 0).sort((a, b) => b.value - a.value), useBytes };
  }

  function renderNetworkDonuts() {
    const rows = netState.domains;
    const { groups: apps, useBytes } = netWeightedGroups(rows, (r) => r.domain);
    const top = apps.slice(0, 5).map((g, i) => ({ label: g.key, value: g.value, color: NET_PALETTE[i] }));
    const rest = apps.slice(5).reduce((s, g) => s + g.value, 0);
    if (rest > 0) top.push({ label: 'Otros', value: rest, color: NET_PALETTE[5] });
    renderDonut('net-donut-apps', top, useBytes ? formatBytes(apps.reduce((s, g) => s + g.value, 0)) : `${apps.length} sitios`);

    const { groups: cats } = netWeightedGroups(rows, (r) => r.category || 'OTHER');
    renderDonut('net-donut-cats', cats.map((g) => ({
      label: NET_CATEGORY_LABELS[g.key] || g.key,
      value: g.value,
      color: NET_CATEGORY_COLORS[g.key] || '#94a3b8',
    })), `${cats.length} cat.`);
  }

  // ---- Banner cards ----
  function renderNetworkBanners() {
    const data = netState.summary;
    if (!data) return;
    const host = netSelectedHost();
    const clients = data.clients || [];
    const sel = host !== 'ALL' ? clients.find((c) => c.agent_id === host) : null;
    const rx = sel ? sel.rx_bytes : data.total_rx_bytes;
    const tx = sel ? sel.tx_bytes : data.total_tx_bytes;
    setText('net-kpi-rx', formatBytes(rx));
    const active = sel ? (sel.total_bytes > 0 ? 1 : 0) : clients.filter((c) => c.total_bytes > 0).length;
    setText('net-kpi-hosts', `${active} Host${active === 1 ? '' : 's'} Activo${active === 1 ? '' : 's'}`);
    setText('net-kpi-hosts-sub', `↑ ${formatBytes(tx)} subidos`);
    setText('net-kpi-flows', `${netState.domains.length} Flujos / Dominios`);
    setText('net-kpi-flows-sub', sel ? `En ${sel.alias}` : 'Registrados en la fecha');
    const { groups } = netWeightedGroups(netState.domains, (r) => r.domain);
    if (groups.length) {
      const topDom = groups[0].key;
      const cat = (netState.domains.find((d) => d.domain === topDom) || {}).category || 'OTHER';
      setText('net-kpi-top-domain', `${topDom} (${cat})`);
      setText('net-kpi-top-domain-sub', NET_CATEGORY_LABELS[cat] || cat);
    } else {
      setText('net-kpi-top-domain', '---');
      setText('net-kpi-top-domain-sub', 'Sin dominios registrados');
    }
    netUpdateLiveIndicators();
  }

  function netBar(value, max, label) {
    const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 0;
    return `<div class="ntop-bar"><div class="ntop-bar-track"><div class="ntop-bar-fill" style="width:${pct}%"></div></div><span class="ntop-bar-val">${label}</span></div>`;
  }

  // Orden estable de filas de clientes: se fija al inicio y no cambia durante las actualizaciones en vivo
  let netClientOrder = [];
  let netClientOrderDate = null;

  function netStableClientOrder(clients, dateKey) {
    const ids = new Set(clients.map((c) => c.agent_id));
    if (netClientOrderDate !== dateKey) {
      netClientOrder = [];
      netClientOrderDate = dateKey;
    }
    if (!netClientOrder.length) {
      // Primera carga: bytes similares (±5%) se desempatan por hostname, usuario y agent_id
      const bucket = (c) => Math.round(Math.log((c.total_bytes || 0) + 1) / Math.log(1.05));
      const sorted = clients.slice().sort((a, b) =>
        (bucket(b) - bucket(a))
        || String(a.hostname || '').toLowerCase().localeCompare(String(b.hostname || '').toLowerCase())
        || String(a.username || '').toLowerCase().localeCompare(String(b.username || '').toLowerCase())
        || String(a.agent_id).localeCompare(String(b.agent_id)));
      netClientOrder = sorted.map((c) => c.agent_id);
    } else {
      // Mantener posiciones existentes, descartar los que ya no existen y añadir nuevos al final
      netClientOrder = netClientOrder.filter((id) => ids.has(id));
      const known = new Set(netClientOrder);
      clients.filter((c) => !known.has(c.agent_id)).forEach((c) => netClientOrder.push(c.agent_id));
    }
    const pos = new Map(netClientOrder.map((id, i) => [id, i]));
    return clients.slice().sort((a, b) => pos.get(a.agent_id) - pos.get(b.agent_id));
  }

  function pktTone(label) {
    const l = String(label || '').toLowerCase();
    if (l.includes('chat') || l.includes('mensajer')) return 'chat';
    if (l.includes(' ia') || l.includes('prompt') || l.includes('consulta')) return 'ai';
    if (l.includes('subida') || l.includes('sincroniz')) return 'up';
    if (l.includes('streaming') || l.includes('multimedia')) return 'stream';
    if (l.includes('descarga')) return 'bulk';
    if (l.includes('gaming')) return 'game';
    if (l.includes('remoto')) return 'remote';
    if (l.includes('captura web') || l.includes('navegaci')) return l.includes('pasiva') ? 'passive' : 'web';
    if (l.includes('redacci')) return 'local';
    if (l.includes('lectura') || l.includes('pasiv')) return 'passive';
    return 'idle';
  }

  function pktBadgeHtml(label) {
    if (!label) return '<span class="pkt-badge pkt-idle">Sin clasificar</span>';
    return `<span class="pkt-badge pkt-${pktTone(label)}" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`;
  }

  function netCrossContextHtml(d) {
    const keys = Number(d.keystrokes_count || 0);
    const kbPill = keys > 0
      ? `<span class="pkt-pill pkt-pill-kb">Teclas: ${keys}</span>`
      : '<span class="pkt-pill">Sin teclado / Pasivo</span>';
    const conf = Number(d.confidence || 0);
    const confPill = conf > 0 && d.last_packet_type
      ? `<span class="pkt-pill pkt-pill-conf ${conf >= 90 ? 'high' : conf >= 80 ? 'mid' : 'low'}">${conf}% match</span>`
      : '';
    const focus = d.focus_app ? `<span class="pkt-pill">Foco: ${escapeHtml(d.focus_app)}</span>` : '';
    return `<div class="pkt-context">${pktBadgeHtml(d.last_packet_type)}<div class="pkt-pills">${focus}${kbPill}${confPill}</div></div>`;
  }

  function buildCorrelationHtml(meta) {
    if (!meta || typeof meta !== 'object') return '';
    const doms = Array.isArray(meta.active_domains) ? meta.active_domains : [];
    const pp = meta.packet_profile && typeof meta.packet_profile === 'object' ? meta.packet_profile : null;
    const rx = Number(meta.rx_kbps || 0);
    const tx = Number(meta.tx_kbps || 0);
    if (!doms.length && !pp && !rx && !tx) return '';
    const domChips = doms
      .map((d) => `<span class="pkt-pill">${escapeHtml(d.domain || '')}</span>`)
      .join('');
    const rate = (rx || tx) ? `<span class="pkt-pill">Red: RX ${escapeHtml(formatBitrate(rx))} / TX ${escapeHtml(formatBitrate(tx))}</span>` : '';
    const focus = meta.app_name ? `<span class="pkt-pill">Foco: ${escapeHtml(meta.app_name)}</span>` : '';
    const pkt = pp && pp.packet_label ? pktBadgeHtml(pp.packet_label) : '';
    const conf = pp && pp.confidence ? `<span class="pkt-pill pkt-pill-conf ${pp.confidence >= 90 ? 'high' : pp.confidence >= 80 ? 'mid' : 'low'}">${Number(pp.confidence)}% match</span>` : '';
    return `<div class="pkt-corr">
      <div class="pkt-corr-title">Correlación Cruzada (Teclado + App en Foco + Tráfico de Red)</div>
      <div class="pkt-pills">${pkt}${conf}${focus}${rate}${domChips}</div>
    </div>`;
  }

  function renderNetworkSummary(data) {
    netState.summary = data;
    // Selector de equipos (conserva la selección)
    if (netDeviceFilter) {
      const cur = netDeviceFilter.value || 'ALL';
      let html = '<option value="ALL">Todos los equipos</option>';
      (data.clients || []).forEach((c) => {
        const label = c.username && c.username !== '---' ? `${c.alias} (${c.username})` : c.alias;
        html += `<option value="${escapeHtml(c.agent_id)}">${escapeHtml(label)}</option>`;
      });
      netDeviceFilter.innerHTML = html;
      netDeviceFilter.value = Array.from(netDeviceFilter.options).some((o) => o.value === cur) ? cur : 'ALL';
    }

    netUpdateActiveHostPill();
    renderTopTalkersLeaderboard();
    renderNetworkHosts();

    if (!netClientsTbody) return;
    const selected = netSelectedHost();
    let clients = data.clients || [];
    if (!clients.length) {
      netClientsTbody.innerHTML = '<tr><td colspan="8" class="empty-row">Sin datos de red.</td></tr>';
      return;
    }
    clients = netStableClientOrder(clients, data.date || ((netDateInput && netDateInput.value) || ''));
    const maxTotal = clients.reduce((m, c) => Math.max(m, c.total_bytes || 0), 0);
    netClientsTbody.innerHTML = clients
      .map((c) => {
        const liveAg = state.agents.get(c.agent_id);
        const online = !!liveAg || c.is_online;
        const rxk = liveAg ? liveAg.rx_kbps : c.rx_kbps;
        const txk = liveAg ? liveAg.tx_kbps : c.tx_kbps;
        const dot = online ? '<span class="status-dot dot-emerald"></span> ' : '';
        const liveRate = online && (rxk || txk)
          ? `<div class="net-kpi-sub">↓ ${formatBitrate(rxk)} · ↑ ${formatBitrate(txk)}</div>`
          : '';
        const topBadge = c.top_domain ? `${netFaviconHtml(c.top_domain_favicon)}<span class="ntop-badge">${escapeHtml(c.top_domain)}</span>` : '---';
        return `<tr class="net-client-row ${c.agent_id === selected ? 'selected' : ''}" data-agent-id="${escapeHtml(c.agent_id)}">
          <td>${dot}<strong>${escapeHtml(c.alias)}</strong><div class="net-kpi-sub">${escapeHtml(c.hostname || '')}${c.username ? ' · ' + escapeHtml(c.username) : ''}</div>${liveRate}</td>
          <td class="net-num">${formatBytes(c.rx_bytes)}</td>
          <td class="net-num">${formatBytes(c.tx_bytes)}</td>
          <td>${netBar(c.total_bytes, maxTotal, formatBytes(c.total_bytes))}</td>
          <td class="net-num">${Math.round(c.peak_rx_kbps)} / ${Math.round(c.peak_tx_kbps)}</td>
          <td class="net-num">${c.domains_count}</td>
          <td>${topBadge}</td>
          <td style="text-align:right"><button type="button" class="net-inspect-btn net-host-inspect-btn" data-agent-id="${escapeHtml(c.agent_id)}">Inspeccionar</button></td>
        </tr>`;
      })
      .join('');
  }

  // ---- Inteligencia IP/Dominio: favicon, bandera (flagcdn, sin emojis), ASN y WHOIS ----
  function netFaviconHtml(url) {
    if (!url) return '';
    return `<img class="net-favicon" src="${escapeHtml(url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.display='none'" />`;
  }

  function netFlagHtml(cc, name) {
    const code = String(cc || '').toUpperCase();
    if (!/^[A-Z]{2}$/.test(code)) return '';
    const img = `<img class="net-flag-img" src="https://flagcdn.com/w20/${code.toLowerCase()}.png" alt="${code}" loading="lazy" onerror="this.style.display='none'" />`;
    return `<span class="net-intel-chip" title="${escapeHtml(name || code)}">${img}<span class="net-cc-badge">[${code}]</span>${name ? ' ' + escapeHtml(name) : ''}</span>`;
  }

  function netIntelHtml(d) {
    const chips = [];
    const flag = netFlagHtml(d.country_code, d.country_name);
    if (flag) chips.push(flag);
    if (d.asn) chips.push(`<span class="net-intel-chip net-intel-asn" title="ASN">${escapeHtml(d.asn)}</span>`);
    const org = d.whois_org || d.org_name;
    if (org) chips.push(`<span class="net-intel-chip net-intel-org" title="WHOIS / Organización">${escapeHtml(org)}</span>`);
    return chips.length ? `<div class="net-intel-row">${chips.join('')}</div>` : '';
  }

  function netDomainCellHtml(d) {
    const raw = d.raw_target || '';
    const shown = raw && d.resolved_domain && d.resolved_domain !== raw ? d.resolved_domain : d.domain;
    const rawTag = raw && shown !== raw ? ` <span class="net-raw-ip">(${escapeHtml(raw)})</span>` : '';
    return `${netFaviconHtml(d.favicon_url)}<strong>${escapeHtml(shown)}</strong>${rawTag}`;
  }

  function netSwitchSubview(viewName) {
    netState.subview = viewName;
    netSubtabBtns.forEach((b) => b.classList.toggle('active', b.dataset.subview === viewName));
    if (netSubviewOverview) netSubviewOverview.classList.toggle('hidden', viewName !== 'overview');
    if (netSubviewHosts) netSubviewHosts.classList.toggle('hidden', viewName !== 'hosts');
    if (netSubviewFlows) netSubviewFlows.classList.toggle('hidden', viewName !== 'flows');

    if (viewName === 'overview') {
      drawTrafficChart();
    } else if (viewName === 'hosts') {
      renderNetworkHosts();
    } else if (viewName === 'flows') {
      renderNetworkDomains();
    }
  }

  function netUpdateActiveHostPill() {
    const host = netSelectedHost();
    if (!netActiveHostPill) return;
    if (host === 'ALL') {
      netActiveHostPill.classList.add('hidden');
    } else {
      const data = netState.summary;
      const client = (data && data.clients) ? data.clients.find((c) => c.agent_id === host) : null;
      const liveAg = state.agents.get(host);
      const name = (client && client.alias) || (liveAg && liveAg.alias) || host;
      if (netActiveHostName) netActiveHostName.textContent = name;
      netActiveHostPill.classList.remove('hidden');
    }
  }

  function renderTopTalkersLeaderboard() {
    if (!netTopTalkersLeaderboard) return;
    const data = netState.summary;
    const clients = (data && data.clients) ? data.clients.slice() : [];
    if (!clients.length) {
      netTopTalkersLeaderboard.innerHTML = '<div class="ntop-empty">Sin datos de equipos para hoy.</div>';
      return;
    }
    clients.sort((a, b) => (b.total_bytes || 0) - (a.total_bytes || 0));
    const top4 = clients.slice(0, 4);
    const maxTotal = top4[0].total_bytes || 1;

    netTopTalkersLeaderboard.innerHTML = top4.map((c, i) => {
      const liveAg = state.agents.get(c.agent_id);
      const online = !!liveAg || c.is_online;
      const rxk = liveAg ? liveAg.rx_kbps : c.rx_kbps;
      const txk = liveAg ? liveAg.tx_kbps : c.tx_kbps;
      const pct = Math.max(4, Math.round(((c.total_bytes || 0) / maxTotal) * 100));
      const dot = online ? '<span class="status-dot dot-emerald"></span>' : '<span class="status-dot dot-gray"></span>';
      return `<div class="net-talker-item" data-agent-id="${escapeHtml(c.agent_id)}">
        <div class="net-talker-head">
          <div class="net-talker-identity">
            <span class="net-talker-rank rank-${i + 1}">#${i + 1}</span>
            <div style="min-width:0">
              <div class="net-talker-name">${dot} ${escapeHtml(c.alias)}</div>
              <div class="net-kpi-sub">${escapeHtml(c.username || c.hostname || '')}</div>
            </div>
          </div>
          <button type="button" class="net-inspect-btn" data-agent-id="${escapeHtml(c.agent_id)}" title="Inspeccionar equipo">Inspeccionar</button>
        </div>
        <div class="net-talker-stats">
          <span>↓ ${formatBytes(c.rx_bytes)} · ↑ ${formatBytes(c.tx_bytes)}</span>
          <strong>${formatBytes(c.total_bytes)}</strong>
        </div>
        <div class="net-talker-progress">
          <div class="net-talker-fill" style="width:${pct}%"></div>
        </div>
        <div class="net-kpi-sub" style="display:flex; justify-content:space-between">
          <span>Velocidad actual:</span>
          <span>↓ ${formatBitrate(rxk || 0)} · ↑ ${formatBitrate(txk || 0)}</span>
        </div>
      </div>`;
    }).join('');
  }

  function renderNetworkHosts() {
    if (!netHostsGrid) return;
    const data = netState.summary;
    let clients = (data && data.clients) ? data.clients.slice() : [];
    if (!clients.length) {
      netHostsGrid.innerHTML = '<div class="ntop-empty">Sin equipos registrados para esta fecha.</div>';
      return;
    }
    const q = netState.hostSearchQuery.toLowerCase();
    if (q) {
      clients = clients.filter((c) =>
        `${c.alias} ${c.hostname || ''} ${c.username || ''} ${c.agent_id}`.toLowerCase().includes(q)
      );
    }
    if (netState.hostSortBy === 'bytes') {
      clients.sort((a, b) => (b.total_bytes || 0) - (a.total_bytes || 0));
    } else if (netState.hostSortBy === 'speed') {
      const speed = (c) => {
        const live = state.agents.get(c.agent_id);
        return ((live ? live.rx_kbps : c.rx_kbps) || 0) + ((live ? live.tx_kbps : c.tx_kbps) || 0);
      };
      clients.sort((a, b) => speed(b) - speed(a));
    } else if (netState.hostSortBy === 'name') {
      clients.sort((a, b) => String(a.alias || '').localeCompare(String(b.alias || '')));
    }

    const maxTotal = clients.reduce((m, c) => Math.max(m, c.total_bytes || 0), 1);
    const selected = netSelectedHost();

    netHostsGrid.innerHTML = clients.map((c) => {
      const liveAg = state.agents.get(c.agent_id);
      const online = !!liveAg || c.is_online;
      const rxk = liveAg ? liveAg.rx_kbps : (c.rx_kbps || 0);
      const txk = liveAg ? liveAg.tx_kbps : (c.tx_kbps || 0);
      const pct = Math.max(3, Math.round(((c.total_bytes || 0) / maxTotal) * 100));
      const dot = online ? '<span class="status-dot dot-emerald"></span>' : '<span class="status-dot dot-gray"></span>';
      const isSel = c.agent_id === selected;
      const topDom = c.top_domain ? `${netFaviconHtml(c.top_domain_favicon)}<strong>${escapeHtml(c.top_domain)}</strong>` : '---';

      return `<div class="net-host-card ${isSel ? 'selected' : ''}" data-agent-id="${escapeHtml(c.agent_id)}">
        <div class="net-host-card-head">
          <div class="net-host-card-identity">
            <div class="net-host-card-alias">${dot} <span>${escapeHtml(c.alias)}</span></div>
            <div class="net-host-card-sub">${escapeHtml(c.hostname || '')}${c.username ? ' · ' + escapeHtml(c.username) : ''}</div>
          </div>
          <span class="badge-pill ${online ? '' : 'muted-badge'}">${online ? 'En Línea' : 'Offline'}</span>
        </div>

        <div class="net-host-speed-row">
          <div class="net-host-speed-box rx">
            <span class="net-host-speed-lbl">↓ Descarga</span>
            <span class="net-host-speed-val">${formatBitrate(rxk)}</span>
          </div>
          <div class="net-host-speed-box tx">
            <span class="net-host-speed-lbl">↑ Subida</span>
            <span class="net-host-speed-val">${formatBitrate(txk)}</span>
          </div>
        </div>

        <div class="net-host-sparkline-wrap">
          <canvas class="net-host-sparkline" data-sparkline-agent="${escapeHtml(c.agent_id)}"></canvas>
        </div>

        <div class="net-talker-progress" title="Consumo relativo: ${pct}%">
          <div class="net-talker-fill" style="width:${pct}%"></div>
        </div>

        <div class="net-talker-stats">
          <span>Acumulado hoy:</span>
          <strong>${formatBytes(c.total_bytes)} (↓ ${formatBytes(c.rx_bytes)} / ↑ ${formatBytes(c.tx_bytes)})</strong>
        </div>

        <div class="net-host-card-focus" title="Dominio principal">
          <span>Sitio líder:</span> ${topDom}
        </div>

        <div class="net-host-card-actions">
          <button type="button" class="btn btn-secondary btn-sm net-host-inspect-btn" data-agent-id="${escapeHtml(c.agent_id)}" style="flex:1">
            Inspeccionar Host
          </button>
          <button type="button" class="btn btn-secondary btn-sm net-host-filter-btn" data-agent-id="${escapeHtml(c.agent_id)}" title="Filtrar matriz de flujos por este host">
            Flujos &rarr;
          </button>
        </div>
      </div>`;
    }).join('');

    requestAnimationFrame(() => {
      document.querySelectorAll('canvas.net-host-sparkline').forEach((cv) => {
        const id = cv.dataset.sparklineAgent;
        const samples = netState.hostHistory.get(id) || [];
        drawSparkline(cv, samples);
      });
    });
  }

  function closeNetDrawer() {
    if (netDrawer) netDrawer.classList.remove('open');
    if (netDrawerBackdrop) netDrawerBackdrop.classList.add('hidden');
  }

  function openNetDrawer(mode, targetId) {
    if (!netDrawer || !netDrawerBody) return;
    if (mode === 'host') {
      const data = netState.summary;
      const client = (data && data.clients) ? data.clients.find((c) => c.agent_id === targetId) : null;
      const liveAg = state.agents.get(targetId);
      const alias = (client && client.alias) || (liveAg && liveAg.alias) || targetId;
      const hostname = (client && client.hostname) || (liveAg && liveAg.hostname) || '---';
      const username = (client && client.username) || (liveAg && liveAg.username) || '---';
      const online = !!liveAg || (client && client.is_online);
      const rxk = liveAg ? liveAg.rx_kbps : ((client && client.rx_kbps) || 0);
      const txk = liveAg ? liveAg.tx_kbps : ((client && client.tx_kbps) || 0);

      setText('net-drawer-title', `Host: ${alias}`);
      if (netDrawerBadge) {
        netDrawerBadge.textContent = 'Equipo / Host';
        netDrawerBadge.className = 'badge-pill';
      }

      const hostDoms = netState.domains.filter((d) => d.agent_id === targetId);
      const { groups: topDoms } = netWeightedGroups(hostDoms, (d) => d.domain);
      const top5Html = topDoms.slice(0, 5).map((g) => {
        const item = hostDoms.find((d) => d.domain === g.key);
        const fav = item ? netFaviconHtml(item.favicon_url) : '';
        return `<li class="drawer-domain-item">
          <span>${fav}<strong>${escapeHtml(g.key)}</strong></span>
          <b class="net-num">${formatBytes(g.value)}</b>
        </li>`;
      }).join('') || '<li class="drawer-domain-item"><span class="ntop-empty">Sin dominios registrados</span></li>';

      let workBytes = 0;
      let leisureBytes = 0;
      let otherBytes = 0;
      hostDoms.forEach((d) => {
        const cat = d.category || 'OTHER';
        const b = d.estimated_bytes || 0;
        if (cat === 'WORK') workBytes += b;
        else if (['STREAMING', 'SOCIAL', 'GAMING', 'SHOPPING'].includes(cat)) leisureBytes += b;
        else otherBytes += b;
      });
      const totCat = workBytes + leisureBytes + otherBytes || 1;
      const pctWork = Math.round((workBytes / totCat) * 100);
      const pctLeisure = Math.round((leisureBytes / totCat) * 100);
      const pctOther = Math.max(0, 100 - pctWork - pctLeisure);

      netDrawerBody.innerHTML = `
        <div class="drawer-card">
          <div class="drawer-card-title">Identidad del Sistema</div>
          <div class="drawer-kv-grid">
            <div class="drawer-kv"><span class="drawer-k">Alias / Nombre</span><span class="drawer-v">${escapeHtml(alias)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Estado</span><span class="drawer-v">${online ? '<span class="status-dot dot-emerald"></span> En línea' : '<span class="status-dot dot-gray"></span> Fuera de línea'}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Hostname</span><span class="drawer-v">${escapeHtml(hostname)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Usuario de Sistema</span><span class="drawer-v">${escapeHtml(username)}</span></div>
            <div class="drawer-kv" style="grid-column:span 2"><span class="drawer-k">ID de Agente</span><span class="drawer-v net-num" style="font-size:11px">${escapeHtml(targetId)}</span></div>
          </div>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Rendimiento de Red en Tiempo Real</div>
          <div class="net-host-speed-row">
            <div class="net-host-speed-box rx">
              <span class="net-host-speed-lbl">↓ Velocidad RX</span>
              <span class="net-host-speed-val">${formatBitrate(rxk)}</span>
            </div>
            <div class="net-host-speed-box tx">
              <span class="net-host-speed-lbl">↑ Velocidad TX</span>
              <span class="net-host-speed-val">${formatBitrate(txk)}</span>
            </div>
          </div>
          <div class="drawer-kv-grid" style="margin-top:6px">
            <div class="drawer-kv"><span class="drawer-k">Total Descargado</span><span class="drawer-v">${formatBytes(client ? client.rx_bytes : 0)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Total Subido</span><span class="drawer-v">${formatBytes(client ? client.tx_bytes : 0)}</span></div>
          </div>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Clasificación de Perfil (Trabajo vs. Ocio)</div>
          <div class="drawer-ratio-track" title="Trabajo: ${pctWork}%, Ocio: ${pctLeisure}%, Otros: ${pctOther}%">
            <div class="drawer-ratio-work" style="width:${pctWork}%"></div>
            <div class="drawer-ratio-leisure" style="width:${pctLeisure}%"></div>
            <div class="drawer-ratio-other" style="width:${pctOther}%"></div>
          </div>
          <div class="net-talker-stats" style="margin-top:4px">
            <span><i class="dot" style="background:var(--emerald)"></i>Trabajo: <b>${pctWork}%</b></span>
            <span><i class="dot" style="background:var(--rose)"></i>Ocio / Social: <b>${pctLeisure}%</b></span>
            <span><i class="dot" style="background:#94a3b8"></i>Otros: <b>${pctOther}%</b></span>
          </div>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Top 5 Destinos más Visitados</div>
          <ul class="drawer-top-domains">
            ${top5Html}
          </ul>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Acciones Directas en el Sistema</div>
          <div class="drawer-actions-row">
            <button type="button" class="btn btn-secondary btn-sm drawer-act-dvr" data-agent-id="${escapeHtml(targetId)}">
              Ver Pantalla / DVR
            </button>
            <button type="button" class="btn btn-secondary btn-sm drawer-act-files" data-agent-id="${escapeHtml(targetId)}">
              Explorador de Archivos
            </button>
            <button type="button" class="btn btn-secondary btn-sm drawer-act-terminal" data-agent-id="${escapeHtml(targetId)}">
              Terminal SSH
            </button>
          </div>
        </div>
      `;
    } else if (mode === 'flow') {
      const flow = netState.domains.find((d) => netFlowKey(d) === targetId);
      if (!flow) return;
      const inbound = flow.direction === 'INBOUND';
      const flag = netFlagHtml(flow.country_code, '');
      const countryTxt = flow.country_name || flow.country_code || 'Global / Anycast';
      const keys = Number(flow.keystrokes_count || 0);
      const apps = flow.apps_list && flow.apps_list !== flow.app_name ? flow.apps_list : '';
      const focus = flow.focus_app || flow.app_name || '---';

      setText('net-drawer-title', flow.domain);
      if (netDrawerBadge) {
        netDrawerBadge.textContent = 'Flujo de Red';
        netDrawerBadge.className = 'badge-pill';
      }

      netDrawerBody.innerHTML = `
        <div class="drawer-card">
          <div class="drawer-card-title">Identidad del Flujo y Destino</div>
          <div class="drawer-kv-grid">
            <div class="drawer-kv"><span class="drawer-k">Dominio / Host</span><span class="drawer-v">${netFaviconHtml(flow.favicon_url)}<strong>${escapeHtml(flow.domain)}</strong></span></div>
            <div class="drawer-kv"><span class="drawer-k">Destino Original / IP</span><span class="drawer-v">${escapeHtml(flow.raw_target || flow.domain)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Puerto</span><span class="drawer-v">:${escapeHtml(String(flow.port || 443))} (${inbound ? 'Entrante al equipo' : 'Saliente'})</span></div>
            <div class="drawer-kv"><span class="drawer-k">Categoría nDPI</span><span class="drawer-v"><span class="net-cat-pill cat-${escapeHtml(flow.category || 'OTHER')}">${escapeHtml(NET_CATEGORY_LABELS[flow.category] || flow.category || 'OTHER')}</span></span></div>
          </div>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Geolocalización e Inteligencia ASN / WHOIS</div>
          <div class="drawer-kv-grid">
            <div class="drawer-kv"><span class="drawer-k">País Destino</span><span class="drawer-v">${flag} ${escapeHtml(countryTxt)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">ASN</span><span class="drawer-v">${escapeHtml(flow.asn || 'Local / Catálogo Directo')}</span></div>
            <div class="drawer-kv" style="grid-column:span 2"><span class="drawer-k">Organización WHOIS / ISP</span><span class="drawer-v">${escapeHtml(flow.whois_org || flow.org_name || 'Servicio verificado en catálogo')}</span></div>
          </div>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Correlación Cruzada (Teclado + Foco + Red)</div>
          <div class="drawer-kv-grid">
            <div class="drawer-kv"><span class="drawer-k">Tipo de Paquete Inferido</span><span class="drawer-v">${flow.last_packet_type ? pktBadgeHtml(flow.last_packet_type) : '<span class="pkt-pill">Lectura / Navegación Pasiva</span>'}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Teclado Registrado</span><span class="drawer-v">${keys > 0 ? `${keys} teclas pulsadas` : '0 teclas (Consumo pasivo)'}</span></div>
            <div class="drawer-kv" style="grid-column:span 2"><span class="drawer-k">App en Foco</span><span class="drawer-v">${escapeHtml(focus)} ${apps ? `<small class="net-kpi-sub">(${escapeHtml(apps)})</small>` : ''}</span></div>
            <div class="drawer-kv" style="grid-column:span 2"><span class="drawer-k">Certeza Algorítmica</span><span class="drawer-v">${Number(flow.confidence || 80)}% match de correlación</span></div>
          </div>
        </div>

        <div class="drawer-card">
          <div class="drawer-card-title">Consumo y Ventana Temporal</div>
          <div class="drawer-kv-grid">
            <div class="drawer-kv"><span class="drawer-k">Descarga (↓ RX)</span><span class="drawer-v">${formatBytes(flow.rx_bytes)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Subida (↑ TX)</span><span class="drawer-v">${formatBytes(flow.tx_bytes)}</span></div>
            <div class="drawer-kv"><span class="drawer-k">Total Acumulado</span><span class="drawer-v">${formatBytes(flow.estimated_bytes)} (${flow.hit_count || 1} hits)</span></div>
            <div class="drawer-kv"><span class="drawer-k">Equipo</span><span class="drawer-v"><strong>${escapeHtml(flow.alias || flow.agent_id)}</strong></span></div>
            <div class="drawer-kv" style="grid-column:span 2"><span class="drawer-k">Ventana de Actividad</span><span class="drawer-v">${escapeHtml(flow.first_seen_at || '---')} &rarr; ${escapeHtml(flow.last_seen_at || '---')}</span></div>
          </div>
        </div>
      `;
    }

    if (netDrawerBackdrop) netDrawerBackdrop.classList.remove('hidden');
    netDrawer.classList.add('open');
  }

  async function loadNetworkIntelStatus() {
    const pill = document.getElementById('net-intel-pill');
    if (!pill) return;
    try {
      const res = await authFetch('/api/network/intel-status');
      const s = await res.json();
      if (!s.ok) return;
      const db = s.pg_connected ? 'PostgreSQL' : 'SQLite local';
      pill.textContent = `Intel DB (${db}): ${s.total_cached} IPs/Dominios · API ${s.requests_last_min}/${s.max_requests_per_min} rpm`;
      pill.title = `Pendientes: ${s.pending} · Última hora: ${s.requests_last_hour}/${s.max_requests_per_hour}`;
      pill.classList.toggle('pg-on', !!s.pg_connected);
      pill.classList.toggle('pg-off', !s.pg_connected);
      pill.classList.toggle('quota-hot', s.requests_last_min >= s.max_requests_per_min);
    } catch (err) {
      pill.textContent = 'Intel DB: sin datos';
    }
  }

  const NET_BG_DAEMONS = ['system idle process', 'svchost.exe', 'msmpeng.exe', 'smartscreen.exe', 'parsecd', 'cloudd', 'rapportd', 'searchtoold', 'assistantd', 'com.apple.geod'];
  const NET_USER_CATS = ['STREAMING', 'SOCIAL', 'MESSAGING', 'AI', 'SHOPPING', 'GAMING', 'REMOTE'];

  function netIsSystemNoise(d, catFilter, q) {
    const cat = d.category || 'OTHER';
    if (d.direction === 'INBOUND' || NET_USER_CATS.includes(cat)) return false;
    if ((d.estimated_bytes || 0) === 0 && cat === 'OTHER') return true;
    if (catFilter === 'SYSTEM' || (q && 'system'.includes(q))) return false;
    if (!['OTHER', 'SYSTEM', 'CLOUD'].includes(cat)) return false;
    const apps = String(d.apps_list || d.app_name || '').split(',').map((a) => a.trim().toLowerCase()).filter(Boolean);
    return apps.length > 0 && apps.every((a) => NET_BG_DAEMONS.includes(a));
  }

  // Estado actual de los filtros de la matriz de flujos
  function netFlowFilterState() {
    return {
      cat: (netCategoryFilter && netCategoryFilter.value) || 'ALL',
      dir: (netDirectionFilter && netDirectionFilter.value) || 'ALL',
      q: netSearchInput ? netSearchInput.value.trim().toLowerCase() : '',
      showSys: !!(netShowSysToggle && netShowSysToggle.checked),
    };
  }

  function netFlowMatchesSearch(d, q) {
    if (!q) return true;
    return `${d.domain} ${d.raw_target || ''} ${d.resolved_domain || ''} ${d.asn || ''} ${d.org_name || ''} ${d.whois_org || ''} ${d.country_code || ''} ${d.app_name} ${d.alias} ${d.hostname}`.toLowerCase().includes(q);
  }

  function netFlowMatches(d, f) {
    if (!f.showSys && netIsSystemNoise(d, f.cat, f.q)) return false;
    if (f.cat !== 'ALL' && (d.category || 'OTHER') !== f.cat) return false;
    if (f.dir === 'INBOUND' && d.direction !== 'INBOUND') return false;
    if (f.dir === 'RX_HEAVY' && !((d.rx_bytes || 0) > (d.tx_bytes || 0))) return false;
    if (f.dir === 'TX_HEAVY' && !((d.tx_bytes || 0) > (d.rx_bytes || 0))) return false;
    return netFlowMatchesSearch(d, f.q);
  }

  function netFilteredFlows() {
    const f = netFlowFilterState();
    const flows = netState.domains.filter((d) => netFlowMatches(d, f));
    const sortBy = (netFlowSortSelect && netFlowSortSelect.value) || 'TOTAL';
    const num = (v) => Number(v) || 0;
    const total = (d) => num(d.estimated_bytes) || (num(d.rx_bytes) + num(d.tx_bytes));
    const comparators = {
      TOTAL: (a, b) => total(b) - total(a),
      RX: (a, b) => num(b.rx_bytes) - num(a.rx_bytes) || total(b) - total(a),
      TX: (a, b) => num(b.tx_bytes) - num(a.tx_bytes) || total(b) - total(a),
      RECENT: (a, b) => String(b.last_seen_at || '').localeCompare(String(a.last_seen_at || '')) || total(b) - total(a),
      KEYS: (a, b) => num(b.keystrokes_count) - num(a.keystrokes_count)
        || num(b.confidence) - num(a.confidence)
        || total(b) - total(a),
    };
    flows.sort(comparators[sortBy] || comparators.TOTAL);
    return flows;
  }

  // ---- Helpers de presentación multi-capa para la matriz de flujos ----
  function netParseTs(ts) {
    const m = String(ts || '').match(/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})/);
    if (!m) return null;
    return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
  }

  function netDurationLabel(first, last) {
    const a = netParseTs(first);
    const b = netParseTs(last);
    if (a === null || b === null || b < a) return '';
    const sec = Math.round((b - a) / 1000);
    if (sec < 60) return `${sec}s`;
    const min = Math.floor(sec / 60);
    if (min < 60) return `${min} min`;
    const h = Math.floor(min / 60);
    return `${h} h ${min % 60} min`;
  }

  function netHiResFavicon(url) {
    if (!url) return '';
    return String(url).replace(/([?&]sz=)\d+/, (m0, prefix) => `${prefix}64`);
  }

  function netPortBadgeHtml(port, inbound) {
    const p = Number(port) || (inbound ? 0 : 443);
    const tone = p === 443 ? 'tls' : p === 80 ? 'http' : 'custom';
    const title = p === 443 ? 'HTTPS / TLS' : p === 80 ? 'HTTP sin cifrar' : 'Puerto personalizado';
    return p ? `<span class="net-port-badge net-port-${tone}" title="${title}">:${p}</span>` : '';
  }

  function netDirBadgeHtml(inbound) {
    return inbound
      ? '<span class="net-dir-badge net-dir-in" title="Conexión entrante al equipo">&darr; Entrante</span>'
      : '<span class="net-dir-badge net-dir-out" title="Conexión saliente desde el equipo">&uarr; Saliente</span>';
  }

  function netDualBarHtml(rx, tx, maxBytes) {
    const r = Math.max(0, Number(rx) || 0);
    const t = Math.max(0, Number(tx) || 0);
    const sum = r + t;
    const scale = maxBytes > 0 ? Math.max(sum > 0 ? 3 : 0, (sum / maxBytes) * 100) : 0;
    const rxPct = sum > 0 ? (r / sum) * scale : 0;
    const txPct = sum > 0 ? (t / sum) * scale : 0;
    const rxShare = sum > 0 ? Math.round((r / sum) * 100) : 0;
    return `<div class="net-dual-bar" title="RX ${rxShare}% / TX ${sum > 0 ? 100 - rxShare : 0}%">
      <div class="net-dual-rx" style="width:${rxPct.toFixed(2)}%"></div><div class="net-dual-tx" style="width:${txPct.toFixed(2)}%"></div>
    </div>`;
  }

  function netOnlineMap() {
    const map = new Map();
    const clients = (netState.summary && netState.summary.clients) || [];
    clients.forEach((c) => map.set(c.agent_id, !!c.is_online));
    state.agents.forEach((a, id) => map.set(a.agent_id || id, true));
    return map;
  }

  function netActivityTag(d) {
    const cat = d.category || 'OTHER';
    const keys = Number(d.keystrokes_count || 0);
    if (cat === 'MESSAGING') return keys > 0 ? 'Chat Activo' : 'Chat Pasivo';
    if (cat === 'STREAMING') return 'Streaming';
    if (cat === 'AI') return 'Consulta IA';
    if (keys > 0) return 'Navegación Activa';
    return 'Pasivo';
  }

  function netShortTime(ts) {
    const t = String(ts || '').trim();
    if (!t) return '---';
    const m = t.match(/^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/);
    if (!m) return t;
    return m[1] === netTodayStr() ? m[2] : `${m[1].slice(5)} ${m[2]}`;
  }

  function netFlowKey(d) {
    return `${d.agent_id}|${d.domain}`;
  }

  function netDetailRowHtml(d) {
    const inbound = d.direction === 'INBOUND';
    const flag = netFlagHtml(d.country_code, '');
    const countryTxt = d.country_name || d.country_code || 'Global / Anycast';
    const keys = Number(d.keystrokes_count || 0);
    const apps = d.apps_list && d.apps_list !== d.app_name ? d.apps_list : '';
    const focus = d.focus_app || d.app_name || '---';
    const kv = (k, v) => `<div class="net-detail-kv"><span class="net-detail-k">${k}</span><span class="net-detail-v">${v}</span></div>`;
    return `<tr class="net-flow-detail-row"><td colspan="5"><div class="net-detail-grid">
      <div class="net-detail-col"><h4>Identidad de Red (ASN / WHOIS / País)</h4>
        ${kv('Destino Original / IP', escapeHtml(d.raw_target || d.domain))}
        ${kv('Puerto', `:${escapeHtml(String(d.port || 443))} (${inbound ? 'Entrante al equipo' : 'Saliente'})`)}
        ${kv('ASN', escapeHtml(d.asn || 'Local / Catálogo Directo'))}
        ${kv('Organización WHOIS / ISP', escapeHtml(d.whois_org || d.org_name || 'Servicio verificado en catálogo'))}
        ${kv('País Origen/Destino', `${flag} ${escapeHtml(countryTxt)}`)}
      </div>
      <div class="net-detail-col"><h4>Correlación Cruzada (Teclado + Foco + Paquete)</h4>
        ${kv('Tipo de Paquete Inferido', d.last_packet_type ? pktBadgeHtml(d.last_packet_type) : '<span class="pkt-pill">Lectura / Navegación Pasiva (RX)</span>')}
        ${kv('Actividad de Teclado', keys > 0 ? `${keys} teclas registradas durante el flujo` : '0 teclas (Consumo pasivo / lectura)')}
        ${kv('App en Foco / Procesos', escapeHtml(focus) + (apps ? ` <span class="net-kpi-sub">${escapeHtml(apps)}</span>` : ''))}
        ${kv('Certeza de Correlación', `${Number(d.confidence || 80)}% de coincidencia (Foco + Teclado + Red)`)}
      </div>
      <div class="net-detail-col"><h4>Desglose de Consumo y Tiempos</h4>
        ${kv('Tráfico Entrante (↓ RX)', formatBytes(d.rx_bytes))}
        ${kv('Tráfico Saliente (↑ TX)', formatBytes(d.tx_bytes))}
        ${kv('Total Acumulado', `${formatBytes(d.estimated_bytes)} en ${d.hit_count || 0} conexiones`)}
        ${kv('Ventana de Actividad', `Primera vez: ${escapeHtml(d.first_seen_at || '---')} · Última vez: ${escapeHtml(d.last_seen_at || '---')}`)}
      </div>
    </div></td></tr>`;
  }

  // Universo base de flujos visibles (solo aplica el filtro de ruido de sistema)
  function netBaseFlowCount() {
    const f = netFlowFilterState();
    const base = { cat: 'ALL', dir: 'ALL', q: '', showSys: f.showSys };
    return netState.domains.reduce((n, d) => n + (netFlowMatches(d, base) ? 1 : 0), 0);
  }

  function netHasActiveFlowFilters() {
    const f = netFlowFilterState();
    return f.cat !== 'ALL' || f.dir !== 'ALL' || f.q !== '';
  }

  // Conteo de flujos por chip de categoria (respeta busqueda y ruido de sistema, no la categoria activa)
  function netRenderCategoryCounts() {
    if (!netCategoryChipsBar) return;
    const f = netFlowFilterState();
    netCategoryChipBtns.forEach((chip) => {
      const chipCat = chip.dataset.category || 'ALL';
      const crit = chipCat === 'INBOUND'
        ? { cat: 'ALL', dir: 'INBOUND', q: f.q, showSys: f.showSys }
        : { cat: chipCat, dir: 'ALL', q: f.q, showSys: f.showSys };
      const count = netState.domains.reduce((n, d) => n + (netFlowMatches(d, crit) ? 1 : 0), 0);
      const badge = chip.querySelector('.net-chip-count');
      if (badge) badge.textContent = String(count);
      chip.classList.toggle('net-chip-empty', count === 0 && chipCat !== 'ALL');
    });
  }

  // Sincroniza chips, pildoras de direccion y botones de limpiar con el estado de los filtros
  function netSyncFlowFilterUi() {
    const f = netFlowFilterState();
    const activeChip = f.cat === 'ALL' && f.dir === 'INBOUND' ? 'INBOUND' : f.cat;
    netCategoryChipBtns.forEach((c) => c.classList.toggle('active', (c.dataset.category || 'ALL') === activeChip));
    netDirectionPillBtns.forEach((b) => {
      const on = b.dataset.dir === f.dir;
      b.classList.toggle('active', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
    if (netSearchClearBtn) netSearchClearBtn.classList.toggle('hidden', !(netSearchInput && netSearchInput.value));
    if (netClearFiltersBtn) netClearFiltersBtn.classList.toggle('hidden', !netHasActiveFlowFilters());
  }

  function renderNetworkDomains() {
    const domains = netFilteredFlows();
    const baseTotal = netBaseFlowCount();
    if (netCounterBadge) {
      netCounterBadge.textContent = `Mostrando ${domains.length} de ${baseTotal} flujos`;
      netCounterBadge.title = domains.length > 300 ? 'La tabla muestra los primeros 300 flujos segun el orden seleccionado' : '';
      netCounterBadge.classList.toggle('is-filtered', domains.length !== baseTotal);
    }
    netSyncFlowFilterUi();
    netRenderCategoryCounts();
    if (!netDomainsTbody) return;
    if (!domains.length) {
      const msg = netHasActiveFlowFilters() ? 'Ningún flujo coincide con los filtros activos.' : 'Sin dominios registrados.';
      netDomainsTbody.innerHTML = `<tr><td colspan="6" class="empty-row">${msg}</td></tr>`;
      return;
    }
    const isToday = ((netDateInput && netDateInput.value) || netTodayStr()) === netTodayStr();
    const liveSet = new Set();
    if (isToday) {
      netState.liveDomains.forEach((list, agentId) => {
        (list || []).forEach((d) => liveSet.add(`${agentId}|${d.domain}`));
      });
    }
    const shown = domains.slice(0, 300);
    const maxBytes = shown.reduce((m, d) => Math.max(m, (Number(d.rx_bytes) || 0) + (Number(d.tx_bytes) || 0), Number(d.estimated_bytes) || 0), 0);
    const onlineMap = netOnlineMap();
    netDomainsTbody.innerHTML = shown
      .map((d) => {
        const cat = d.category || 'OTHER';
        const catColor = NET_CATEGORY_COLORS[cat] || NET_CATEGORY_COLORS.OTHER;
        const key = netFlowKey(d);
        const isLive = liveSet.has(key);
        const inbound = d.direction === 'INBOUND';
        const raw = d.raw_target || '';
        const displayDom = (d.resolved_domain && d.resolved_domain !== d.raw_target) ? d.resolved_domain : d.domain;
        const targetTxt = raw && raw !== displayDom ? raw : (raw || d.domain || '');
        const cc = String(d.country_code || '').toUpperCase();
        const flag = /^[A-Z]{2}$/.test(cc)
          ? `<span class="net-intel-chip net-geo-chip" title="${escapeHtml(d.country_name || cc)}"><img class="net-flag-img" src="https://flagcdn.com/w40/${cc.toLowerCase()}.png" alt="${cc}" loading="lazy" onerror="this.style.display='none'" /><span class="net-cc-badge">[${cc}]</span></span>`
          : '';
        const org = d.whois_org || d.org_name || '';
        const asnChip = d.asn || org
          ? `<span class="net-net-chip" title="${escapeHtml([d.asn, org].filter(Boolean).join(' / '))}">${d.asn ? `<span class="net-net-chip-asn">${escapeHtml(d.asn)}</span>` : ''}${org ? `<span class="net-net-chip-org">${escapeHtml(org)}</span>` : ''}</span>`
          : '';
        const fav = d.favicon_url
          ? `<img class="net-favicon-hd" src="${escapeHtml(netHiResFavicon(d.favicon_url))}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.style.visibility='hidden'" />`
          : `<span class="net-favicon-fallback">${escapeHtml(String(displayDom || '?').charAt(0).toUpperCase())}</span>`;

        const online = !!onlineMap.get(d.agent_id);
        const proc = d.focus_app || d.app_name || '';
        const apps = d.apps_list && d.apps_list !== proc ? d.apps_list : '';
        const keys = Number(d.keystrokes_count || 0);
        const kbHtml = keys > 0
          ? `<span class="net-kb-ind net-kb-active">${keys.toLocaleString('es-MX')} teclas</span>`
          : '<span class="net-kb-ind net-kb-passive">Navegación pasiva</span>';

        const conf = Number(d.confidence || 0);
        const confTone = conf >= 90 ? 'high' : conf >= 80 ? 'mid' : 'low';
        const pkt = d.last_packet_type ? pktBadgeHtml(d.last_packet_type) : `<span class="pkt-badge pkt-${pktTone(netActivityTag(d))}">${escapeHtml(netActivityTag(d))}</span>`;
        const confHtml = conf > 0
          ? `<span class="net-conf-meter ${confTone}" title="Certeza de correlación"><span class="net-conf-track"><span class="net-conf-fill" style="width:${Math.min(100, conf)}%"></span></span><span class="net-conf-val">${conf}%</span></span>`
          : '';

        const rx = Number(d.rx_bytes || 0);
        const tx = Number(d.tx_bytes || 0);
        const est = Number(d.estimated_bytes || 0) || rx + tx;
        const hits = Number(d.hit_count || 0);
        const dur = netDurationLabel(d.first_seen_at, d.last_seen_at);

        return `<tr class="net-flow-row ${isLive ? 'net-live-row' : ''}" data-flow-key="${escapeHtml(key)}">
          <td class="net-cell-dest">
            <div class="net-dest-wrap">
              <div class="net-fav-box">${fav}</div>
              <div class="net-stack">
                <div class="net-dest-main"><strong class="net-dest-domain" title="${escapeHtml(displayDom)}">${escapeHtml(displayDom)}</strong>${isLive ? '<span class="ntop-badge ntop-badge-live">EN VIVO</span>' : ''}</div>
                <div class="net-dest-sub"><span class="net-target-txt" title="${escapeHtml(targetTxt)}">${escapeHtml(targetTxt)}</span>${netPortBadgeHtml(d.port, inbound)}${netDirBadgeHtml(inbound)}</div>
                ${asnChip || flag ? `<div class="net-dest-sub net-dest-intel">${flag}${asnChip}</div>` : ''}
              </div>
            </div>
          </td>
          <td class="net-cell-host">
            <div class="net-stack">
              <div class="net-host-main"><span class="status-dot ${online ? 'dot-emerald' : 'dot-gray'}" title="${online ? 'En línea' : 'Fuera de línea'}"></span><strong>${escapeHtml(d.alias || d.agent_id)}</strong></div>
              <div><span class="net-proc-badge" title="${escapeHtml(apps ? `${proc} (${apps})` : proc)}">${escapeHtml(proc || 'Proceso desconocido')}</span></div>
              <div>${kbHtml}</div>
            </div>
          </td>
          <td class="net-cell-dpi">
            <div class="net-stack">
              <div><span class="net-cat-pill cat-${escapeHtml(cat)}" style="--cat-color:${catColor}"><span class="net-cat-dot"></span>${escapeHtml(NET_CATEGORY_LABELS[cat] || cat)}</span></div>
              <div class="net-dpi-row">${pkt}${confHtml}</div>
            </div>
          </td>
          <td class="net-cell-traffic">
            <div class="net-stack">
              ${netDualBarHtml(rx, tx, maxBytes)}
              <div class="net-rxtx-figs"><span class="net-fig-rx">&darr; ${formatBytes(rx)}</span><span class="net-fig-tx">&uarr; ${formatBytes(tx)}</span></div>
              <div class="net-total-line"><strong>${formatBytes(est)}</strong><span class="net-hits">${hits.toLocaleString('es-MX')} hits</span></div>
            </div>
          </td>
          <td class="net-cell-activity">
            <div class="net-stack net-time-stack">
              <div class="net-time-row"><span class="net-time-k">Última</span><span class="net-time-v">${escapeHtml(netShortTime(d.last_seen_at))}</span></div>
              <div class="net-time-row"><span class="net-time-k">Primera</span><span class="net-time-v muted">${escapeHtml(netShortTime(d.first_seen_at))}</span></div>
              ${dur ? `<div class="net-time-row"><span class="net-time-k">Duración</span><span class="net-time-v muted">${dur}</span></div>` : ''}
            </div>
          </td>
          <td class="net-cell-inspector"><button type="button" class="net-inspect-btn net-inspect-flow-btn" data-flow-key="${escapeHtml(key)}">Inspeccionar</button></td>
        </tr>`;
      })
      .join('');
  }

  function renderNetworkAll() {
    renderNetworkBanners();
    renderNetworkDonuts();
    renderTopTalkersLeaderboard();
    renderNetworkHosts();
    renderNetworkDomains();
    drawTrafficChart();
    netUpdateActiveHostPill();
  }

  function netMarkUpdated(ok) {
    const stamp = document.getElementById('net-last-update');
    if (stamp) stamp.textContent = ok ? `· Última actualización ${new Date().toLocaleTimeString('es-MX', { hour12: false })}` : '· Error al actualizar';
  }

  function netApplyAutoUi() {
    if (netLiveInd) netLiveInd.classList.toggle('paused', !netState.auto);
    setText('net-live-indicator', netState.auto ? 'Tiempo Real (WebSocket)' : 'Feed en vivo en pausa');
  }

  async function loadNetworkDashboard(opts = {}) {
    const silent = !!opts.silent;
    if (netState.loading) return;
    netState.loading = true;
    netLastSyncAt = Date.now();
    if (netDateInput && !netDateInput.value) netDateInput.value = netTodayStr();
    if (!silent && refreshNetBtn) refreshNetBtn.classList.add('loading');
    try {
      const domParams = netBaseParams();
      domParams.set('agent_id', netSelectedHost());
      domParams.set('category', 'ALL');
      domParams.set('q', '');
      domParams.set('limit', '1000');
      const [sumRes, domRes] = await Promise.all([
        authFetch(`/api/network/summary?${netBaseParams().toString()}`),
        authFetch(`/api/network/domains?${domParams.toString()}`),
      ]);
      const sumData = await sumRes.json();
      const domData = await domRes.json();
      if (sumData.ok) renderNetworkSummary(sumData);
      if (domData.ok) netState.domains = domData.domains || [];
      renderNetworkAll();
      netMarkUpdated(true);
      loadNetworkIntelStatus();
      if (!silent && refreshNetBtn) {
        refreshNetBtn.classList.add('done');
        setTimeout(() => refreshNetBtn.classList.remove('done'), 700);
      }
    } catch (err) {
      console.error('Error al consultar tráfico de red:', err);
      netMarkUpdated(false);
    } finally {
      netState.loading = false;
      if (refreshNetBtn) refreshNetBtn.classList.remove('loading');
    }
  }

  // Compatibilidad con switchView
  function loadNetworkView() {
    return loadNetworkDashboard();
  }

  // ---- Motor de tiempo real por WebSocket (sin polling HTTP periodico) ----
  function netLocalStamp(date = new Date()) {
    const pad = (x) => String(x).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  }

  function netIsViewingToday() {
    return ((netDateInput && netDateInput.value) || netTodayStr()) === netTodayStr();
  }

  // Inserta o actualiza en memoria los flujos recibidos por WebSocket (sin consultar la BD)
  function netUpsertLiveDomains(agent, list) {
    if (!agent || !Array.isArray(list) || !list.length) return false;
    if (!netIsViewingToday()) return false;
    const host = netSelectedHost();
    if (host !== 'ALL' && agent.agent_id !== host) return false;
    if (state.tenantFilter && state.tenantFilter !== 'ALL' && agent.tenant_id && agent.tenant_id !== state.tenantFilter) return false;

    const index = new Map();
    netState.domains.forEach((d, i) => index.set(netFlowKey(d), i));
    const stamp = netLocalStamp();
    let changed = false;

    list.forEach((item) => {
      if (!item || !item.domain) return;
      const est = Math.max(0, Number(item.estimated_bytes) || 0);
      const hasSplit = item.rx_bytes !== null && item.rx_bytes !== undefined
        || item.tx_bytes !== null && item.tx_bytes !== undefined;
      let rx = hasSplit ? Math.max(0, Number(item.rx_bytes) || 0) : Math.round(est * 0.85);
      let tx = hasSplit ? Math.max(0, Number(item.tx_bytes) || 0) : est - Math.round(est * 0.85);
      if (hasSplit && rx + tx === 0 && est > 0) {
        rx = Math.round(est * 0.85);
        tx = est - rx;
      }
      const total = Math.max(est, rx + tx);
      const hits = Math.max(1, Number(item.connections) || 1);
      const inbound = String(item.direction || '').toUpperCase() === 'INBOUND';
      const key = `${agent.agent_id}|${item.domain}`;
      const pos = index.get(key);

      if (pos !== undefined) {
        const d = netState.domains[pos];
        d.rx_bytes = (Number(d.rx_bytes) || 0) + rx;
        d.tx_bytes = (Number(d.tx_bytes) || 0) + tx;
        d.estimated_bytes = (Number(d.estimated_bytes) || 0) + total;
        d.hit_count = (Number(d.hit_count) || 0) + hits;
        d.last_seen_at = stamp;
        if (item.category) d.category = item.category;
        if (inbound) {
          d.direction = 'INBOUND';
          if (item.port) d.port = item.port;
        }
      } else {
        netState.domains.push({
          agent_id: agent.agent_id,
          alias: agent.alias || agent.hostname || agent.agent_id,
          hostname: agent.hostname || '',
          domain: item.domain,
          app_name: item.app_name || '',
          apps_list: item.app_name || '',
          category: item.category || 'OTHER',
          hit_count: hits,
          estimated_bytes: total,
          rx_bytes: rx,
          tx_bytes: tx,
          direction: inbound ? 'INBOUND' : 'OUTBOUND',
          port: item.port || 443,
          first_seen_at: stamp,
          last_seen_at: stamp,
        });
        index.set(key, netState.domains.length - 1);
      }
      changed = true;
    });
    return changed;
  }

  // Redibuja solo la sub-vista visible
  function netRenderActiveView() {
    if (state.currentView !== 'network') return;
    netState.lastRender = Date.now();
    if (netState.subview === 'flows') {
      renderNetworkDomains();
    } else if (netState.subview === 'hosts') {
      renderNetworkHosts();
    } else {
      renderNetworkDonuts();
      renderTopTalkersLeaderboard();
      setText('net-kpi-flows', `${netState.domains.length} Flujos / Dominios`);
    }
    netMarkUpdated(true);
  }

  // Agrupa rafagas de telemetria de varios agentes en un solo render (maximo uno cada 800ms)
  function netScheduleLiveRender() {
    if (netWsRenderTimer) return;
    netWsRenderTimer = setTimeout(() => {
      netWsRenderTimer = null;
      netRenderActiveView();
    }, NET_WS_RENDER_DEBOUNCE_MS);
  }

  // Evento en vivo desde WebSocket (agent_telemetry)
  function netOnLiveTelemetry(msg) {
    if (state.currentView !== 'network') return;
    const ag = msg && msg.agent;
    if (ag && Array.isArray(msg.recent_domains)) {
      netState.liveDomains.set(ag.agent_id, msg.recent_domains);
    }
    netUpdateLiveIndicators();
    if (!netState.auto) return;
    if (ag && Array.isArray(msg.recent_domains) && netUpsertLiveDomains(ag, msg.recent_domains)) {
      netScheduleLiveRender();
    }
  }

  // Evento new_log de categoria NETWORK: sincronizacion suave con debounce
  function netOnNetworkLog() {
    if (state.currentView !== 'network' || !netState.auto) return;
    clearTimeout(netState.refreshTimer);
    netState.refreshTimer = setTimeout(() => loadNetworkDashboard({ silent: true }), NET_LOG_SYNC_DEBOUNCE_MS);
  }

  // Muestreo local cada 4s: historial de velocidad, indicadores y grafica (sin peticiones HTTP)
  setInterval(() => {
    netPushSample();
    if (state.currentView !== 'network') return;
    netUpdateLiveIndicators();
    drawTrafficChart();
  }, NET_TICK_MS);

  // Respaldo relajado: sincroniza con la BD cada 45s solo si el feed en vivo esta activo
  setInterval(() => {
    if (state.currentView !== 'network' || !netState.auto || document.hidden) return;
    if (Date.now() - netLastSyncAt < NET_BACKUP_SYNC_MS - 1000) return;
    loadNetworkDashboard({ silent: true });
  }, NET_BACKUP_SYNC_MS);
  window.addEventListener('resize', () => { if (state.currentView === 'network') drawTrafficChart(); });

  // Canvas hover scrubbing para tooltip interactivo
  if (netCanvas) {
    netCanvas.addEventListener('mousemove', (ev) => {
      const rect = netCanvas.getBoundingClientRect();
      const x = ev.clientX - rect.left;
      const w = netCanvas.clientWidth;
      const padL = 64;
      const padR = 12;
      const cw = w - padL - padR;
      const hist = netState.history;
      if (!hist.length || x < padL || x > w - padR) {
        if (netChartTooltip) netChartTooltip.classList.add('hidden');
        return;
      }
      const tMax = hist[hist.length - 1].t;
      const tMin = Math.min(hist[0].t, tMax - NET_TICK_MS * 30);
      const ratio = Math.max(0, Math.min(1, (x - padL) / cw));
      const targetT = tMin + ratio * (tMax - tMin);
      let closest = hist[0];
      let minDiff = Infinity;
      hist.forEach((p) => {
        const diff = Math.abs(p.t - targetT);
        if (diff < minDiff) {
          minDiff = diff;
          closest = p;
        }
      });
      if (netChartTooltip && closest) {
        const timeStr = new Date(closest.t).toLocaleTimeString('es-MX', { hour12: false });
        netChartTooltip.innerHTML = `
          <div class="net-chart-tooltip-time">${timeStr}</div>
          <div class="net-chart-tooltip-val">
            <span class="net-chart-tooltip-rx">↓ Descarga: ${formatBitrate(closest.rx)}</span>
            <span class="net-chart-tooltip-tx">↑ Subida: ${formatBitrate(closest.tx)}</span>
          </div>
        `;
        netChartTooltip.style.left = `${x}px`;
        netChartTooltip.style.top = `${ev.clientY - rect.top}px`;
        netChartTooltip.classList.remove('hidden');
      }
    });
    netCanvas.addEventListener('mouseleave', () => {
      if (netChartTooltip) netChartTooltip.classList.add('hidden');
    });
  }

  // Subnavegación segmentada
  netSubtabBtns.forEach((btn) => {
    btn.addEventListener('click', () => netSwitchSubview(btn.dataset.subview));
  });

  if (netClearHostBtn) {
    netClearHostBtn.addEventListener('click', () => {
      if (netDeviceFilter) netDeviceFilter.value = 'ALL';
      netUpdateActiveHostPill();
      loadNetworkDashboard({ silent: true });
    });
  }

  netChartRangeBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      netChartRangeBtns.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      netState.chartRange = btn.dataset.range;
      drawTrafficChart();
    });
  });

  if (netGotoHostsBtn) {
    netGotoHostsBtn.addEventListener('click', () => netSwitchSubview('hosts'));
  }

  if (netClientsTbody) {
    netClientsTbody.addEventListener('click', (ev) => {
      const inspectBtn = ev.target.closest('.net-host-inspect-btn');
      if (inspectBtn) {
        openNetDrawer('host', inspectBtn.dataset.agentId);
        return;
      }
      const tr = ev.target.closest('tr.net-client-row');
      if (!tr || !netDeviceFilter) return;
      netDeviceFilter.value = netDeviceFilter.value === tr.dataset.agentId ? 'ALL' : tr.dataset.agentId;
      netUpdateActiveHostPill();
      loadNetworkDashboard({ silent: true });
    });
  }

  if (netTopTalkersLeaderboard) {
    netTopTalkersLeaderboard.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.net-inspect-btn');
      const item = ev.target.closest('.net-talker-item');
      const agentId = (btn && btn.dataset.agentId) || (item && item.dataset.agentId);
      if (agentId) openNetDrawer('host', agentId);
    });
  }

  if (netHostsGrid) {
    netHostsGrid.addEventListener('click', (ev) => {
      const inspectBtn = ev.target.closest('.net-host-inspect-btn');
      if (inspectBtn) {
        openNetDrawer('host', inspectBtn.dataset.agentId);
        return;
      }
      const filterBtn = ev.target.closest('.net-host-filter-btn');
      if (filterBtn) {
        if (netDeviceFilter) netDeviceFilter.value = filterBtn.dataset.agentId;
        netUpdateActiveHostPill();
        netSwitchSubview('flows');
        loadNetworkDashboard({ silent: true });
        return;
      }
      const card = ev.target.closest('.net-host-card');
      if (card && card.dataset.agentId) {
        openNetDrawer('host', card.dataset.agentId);
      }
    });
  }

  if (netHostSearchInput) {
    netHostSearchInput.addEventListener('input', () => {
      netState.hostSearchQuery = netHostSearchInput.value.trim();
      renderNetworkHosts();
    });
  }

  if (netHostSortSelect) {
    netHostSortSelect.addEventListener('change', () => {
      netState.hostSortBy = netHostSortSelect.value;
      renderNetworkHosts();
    });
  }

  if (netHostViewCardsBtn && netHostViewTableBtn) {
    netHostViewCardsBtn.addEventListener('click', () => {
      netHostViewCardsBtn.classList.add('active');
      netHostViewTableBtn.classList.remove('active');
      if (netHostsGrid) netHostsGrid.classList.remove('hidden');
      if (netHostsTableWrap) netHostsTableWrap.classList.add('hidden');
    });
    netHostViewTableBtn.addEventListener('click', () => {
      netHostViewTableBtn.classList.add('active');
      netHostViewCardsBtn.classList.remove('active');
      if (netHostsGrid) netHostsGrid.classList.add('hidden');
      if (netHostsTableWrap) netHostsTableWrap.classList.remove('hidden');
    });
  }

  if (netDomainsTbody) {
    netDomainsTbody.addEventListener('click', (ev) => {
      const tr = ev.target.closest('tr.net-flow-row');
      if (!tr) return;
      const key = tr.dataset.flowKey;
      if (key) openNetDrawer('flow', key);
    });
  }
  if (netShowSysToggle) netShowSysToggle.addEventListener('change', renderNetworkDomains);
  if (netDateInput) netDateInput.addEventListener('change', () => loadNetworkDashboard());
  if (netDeviceFilter) netDeviceFilter.addEventListener('change', () => loadNetworkDashboard({ silent: true }));
  if (netCategoryFilter) netCategoryFilter.addEventListener('change', renderNetworkDomains);
  if (netDirectionFilter) netDirectionFilter.addEventListener('change', renderNetworkDomains);
  if (netFlowSortSelect) netFlowSortSelect.addEventListener('change', renderNetworkDomains);
  let netSearchTimer = null;
  if (netSearchInput) {
    netSearchInput.addEventListener('input', () => {
      if (netSearchClearBtn) netSearchClearBtn.classList.toggle('hidden', !netSearchInput.value);
      clearTimeout(netSearchTimer);
      netSearchTimer = setTimeout(renderNetworkDomains, 150);
    });
    netSearchInput.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape' && netSearchInput.value) {
        netSearchInput.value = '';
        clearTimeout(netSearchTimer);
        renderNetworkDomains();
      }
    });
  }
  if (netSearchClearBtn) {
    netSearchClearBtn.addEventListener('click', () => {
      if (netSearchInput) {
        netSearchInput.value = '';
        netSearchInput.focus();
      }
      clearTimeout(netSearchTimer);
      renderNetworkDomains();
    });
  }
  netDirectionPillBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      if (netDirectionFilter) netDirectionFilter.value = btn.dataset.dir || 'ALL';
      renderNetworkDomains();
    });
  });
  if (netClearFiltersBtn) {
    netClearFiltersBtn.addEventListener('click', () => {
      if (netCategoryFilter) netCategoryFilter.value = 'ALL';
      if (netDirectionFilter) netDirectionFilter.value = 'ALL';
      if (netSearchInput) netSearchInput.value = '';
      clearTimeout(netSearchTimer);
      renderNetworkDomains();
    });
  }
  if (netAutoToggle) {
    netAutoToggle.addEventListener('change', () => {
      netState.auto = netAutoToggle.checked;
      netApplyAutoUi();
      if (netState.auto) loadNetworkDashboard({ silent: true });
    });
  }
  if (refreshNetBtn) refreshNetBtn.addEventListener('click', () => loadNetworkDashboard());
  if (exportNetCsvBtn) {
    exportNetCsvBtn.addEventListener('click', () => {
      const params = netFilterParams();
      params.set('admin_pass', state.password || '');
      window.location.href = `/api/network/export-csv?${params.toString()}`;
    });
  }
  netApplyAutoUi();

  // Chips rápidos de categoría
  netCategoryChipBtns.forEach((chip) => {
    chip.addEventListener('click', () => {
      const cat = chip.dataset.category || 'ALL';
      if (cat === 'INBOUND') {
        if (netCategoryFilter) netCategoryFilter.value = 'ALL';
        if (netDirectionFilter) netDirectionFilter.value = 'INBOUND';
      } else {
        if (netCategoryFilter) netCategoryFilter.value = cat;
        if (netDirectionFilter && netDirectionFilter.value === 'INBOUND') {
          netDirectionFilter.value = 'ALL';
        }
      }
      renderNetworkDomains();
    });
  });

  // Eventos del Inspector Drawer lateral
  if (netDrawerClose) netDrawerClose.addEventListener('click', closeNetDrawer);
  if (netDrawerBackdrop) netDrawerBackdrop.addEventListener('click', closeNetDrawer);
  document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape' && netDrawer && netDrawer.classList.contains('open')) {
      closeNetDrawer();
    }
  });

  if (netDrawerBody) {
    netDrawerBody.addEventListener('click', (ev) => {
      const dvrBtn = ev.target.closest('.drawer-act-dvr');
      if (dvrBtn) {
        closeNetDrawer();
        openViewerForAgent(dvrBtn.dataset.agentId);
        return;
      }
      const filesBtn = ev.target.closest('.drawer-act-files');
      if (filesBtn) {
        closeNetDrawer();
        if (typeof openFinderExplorer === 'function') openFinderExplorer(filesBtn.dataset.agentId);
        return;
      }
      const termBtn = ev.target.closest('.drawer-act-terminal');
      if (termBtn) {
        closeNetDrawer();
        if (typeof openReverseTerminal === 'function') openReverseTerminal(termBtn.dataset.agentId);
        return;
      }
    });
  }

  tabWall.addEventListener('click', () => switchView('wall'));
  tabViewer.addEventListener('click', () => {
    if (state.selectedAgentId) {
      openViewerForAgent(state.selectedAgentId);
    }
  });
  tabActivity.addEventListener('click', () => switchView('activity'));
  tabLogs.addEventListener('click', () => switchView('logs'));
  if (tabNetwork) tabNetwork.addEventListener('click', () => switchView('network'));
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

  if (groupLogsToggleBtn) {
    groupLogsToggleBtn.addEventListener('click', () => {
      state.groupConsecutiveLogs = !state.groupConsecutiveLogs;
      groupLogsToggleBtn.classList.toggle('active', state.groupConsecutiveLogs);
      if (groupLogsLabel) {
        groupLogsLabel.textContent = state.groupConsecutiveLogs ? 'Agrupar' : 'Sin agrupar';
      }
      renderLogsTable();
    });
  }

  if (logCategoryChips && logCategoryFilter) {
    logCategoryChips.addEventListener('click', (e) => {
      const chip = e.target.closest('.log-cat-chip');
      if (!chip) return;
      logCategoryFilter.value = chip.dataset.cat || 'ALL';
      syncLogCategoryChips();
      fetchLogsFromServer();
    });
  }

  document.addEventListener('click', (e) => {
    document.querySelectorAll('details.card-more-menu[open]').forEach((menu) => {
      if (!menu.contains(e.target) || e.target.closest('.card-more-item')) {
        menu.removeAttribute('open');
      }
    });
  });

  if (closeEvidenceModalBtn) {
    closeEvidenceModalBtn.addEventListener('click', closeEvidenceModal);
  }
  if (evidenceModalCloseBtn) {
    evidenceModalCloseBtn.addEventListener('click', closeEvidenceModal);
  }
  if (evidenceModal) {
    evidenceModal.addEventListener('click', (e) => {
      if (e.target === evidenceModal) closeEvidenceModal();
    });
  }
  [evidencePrevBtn, evidenceStagePrev].forEach((b) => {
    if (b) b.addEventListener('click', () => stepEvidenceModal(-1));
  });
  [evidenceNextBtn, evidenceStageNext].forEach((b) => {
    if (b) b.addEventListener('click', () => stepEvidenceModal(1));
  });
  if (evidenceHdBtn) {
    evidenceHdBtn.addEventListener('click', () => {
      evidenceIsHd = !evidenceIsHd;
      syncEvidenceQualityUI();
    });
  }
  document.addEventListener('keydown', (e) => {
    if (evidenceModal && !evidenceModal.classList.contains('hidden')) {
      if (e.key === 'Escape') {
        closeEvidenceModal();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        stepEvidenceModal(-1);
      } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        stepEvidenceModal(1);
      }
    }
  });

  if (logsTbody) {
    logsTbody.addEventListener('click', (e) => {
      const devChip = e.target.closest('.js-log-filter-device');
      if (devChip && logDeviceFilter) {
        const targetId = devChip.dataset.agentId || 'ALL';
        logDeviceFilter.value = logDeviceFilter.value === targetId ? 'ALL' : targetId;
        fetchLogsFromServer();
        return;
      }
      if (e.target.closest('summary') || e.target.closest('details')) return;
      const cardEl = e.target.closest('[data-log-id]');
      if (cardEl) {
        e.preventDefault();
        const chronoLogs = Array.isArray(state.logsCache) ? state.logsCache.slice().reverse() : [];
        const logIdx = chronoLogs.findIndex((r) => String(r.id) === String(cardEl.dataset.logId));
        if (logIdx >= 0) {
          const payload = logRowToEvidencePayload(chronoLogs[logIdx], chronoLogs);
          if (payload) openEvidenceModal(payload, { list: chronoLogs, index: logIdx });
          return;
        }
      }
    });
  }

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

  if (openFileBtn) {
    openFileBtn.addEventListener('click', () => {
      if (state.selectedAgentId) openFinderExplorer(state.selectedAgentId);
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
      if (stDatePicker && metricsDateInput) stDatePicker.value = metricsDateInput.value;
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

  // =====================================================================
  // TERMINAL INVERSA SILENCIOSA SOBRE WSS (MF-SH06 / MF-SH07)
  // =====================================================================
  const terminalModal = document.getElementById('terminal-modal');
  const viewerTerminalBtn = document.getElementById('viewer-terminal-btn');
  const rshDeviceTitle = document.getElementById('rsh-device-title');
  const rshShellStatus = document.getElementById('rsh-shell-status');
  const rshShellSelect = document.getElementById('rsh-shell-select');
  const rshCtrlcBtn = document.getElementById('rsh-ctrlc-btn');
  const rshClearBtn = document.getElementById('rsh-clear-btn');
  const rshCloseBtn = document.getElementById('rsh-close-btn');
  const rshOutput = document.getElementById('rsh-output');
  const rshForm = document.getElementById('rsh-form');
  const rshInput = document.getElementById('rsh-input');
  let rshActiveAgentId = null;
  let rshActiveIsMac = false;
  const rshHistory = [];
  let rshHistIdx = -1;

  function stripAnsiSequences(raw) {
    return String(raw || '')
      .replace(/\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)/g, '')
      .replace(/\x1b\[[0-9;?]*[ -/]*[@-~]/g, '')
      .replace(/\x1b[@-_]/g, '')
      .replace(/^[ \t]+\r/gm, '')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '');
  }

  function appendTerminalOutput(chunk) {
    if (!rshOutput) return;
    const clean = stripAnsiSequences(chunk);
    if (!clean) return;
    rshOutput.textContent = (rshOutput.textContent + clean).slice(-90000);
    rshOutput.scrollTop = rshOutput.scrollHeight;
  }

  function openReverseTerminal(agentId) {
    if (!terminalModal || !agentId) return;
    const ag = state.agents.get(agentId);
    if (!ag) return;
    if (rshActiveAgentId && rshActiveAgentId !== agentId) {
      sendWsJson({ type: 'shell_close', agent_id: rshActiveAgentId });
    }
    rshActiveAgentId = agentId;
    rshActiveIsMac = /darwin|mac/i.test(String(ag.os || ''));
    if (rshDeviceTitle) rshDeviceTitle.textContent = `Terminal Inversa — ${ag.alias || ag.hostname}`;
    if (rshShellStatus) rshShellStatus.textContent = 'Abriendo sesion inversa silenciosa...';
    if (rshOutput) rshOutput.textContent = '';
    terminalModal.classList.remove('hidden');
    sendWsJson({
      type: 'shell_open',
      agent_id: agentId,
      shell_type: rshShellSelect ? rshShellSelect.value : 'auto',
    });
    setTimeout(() => rshInput && rshInput.focus(), 60);
  }

  function closeReverseTerminal() {
    if (rshActiveAgentId) {
      sendWsJson({ type: 'shell_close', agent_id: rshActiveAgentId });
      rshActiveAgentId = null;
    }
    if (terminalModal) terminalModal.classList.add('hidden');
  }

  if (viewerTerminalBtn) {
    viewerTerminalBtn.addEventListener('click', () => {
      if (state.selectedAgentId) openReverseTerminal(state.selectedAgentId);
    });
  }
  if (rshCloseBtn) rshCloseBtn.addEventListener('click', closeReverseTerminal);
  if (rshClearBtn) {
    rshClearBtn.addEventListener('click', () => {
      if (rshOutput) rshOutput.textContent = '';
      if (rshInput) rshInput.focus();
    });
  }
  if (rshCtrlcBtn) {
    rshCtrlcBtn.addEventListener('click', () => {
      if (rshActiveAgentId) sendWsJson({ type: 'shell_interrupt', agent_id: rshActiveAgentId });
      if (rshInput) rshInput.focus();
    });
  }
  if (rshShellSelect) {
    rshShellSelect.addEventListener('change', () => {
      if (rshActiveAgentId) openReverseTerminal(rshActiveAgentId);
    });
  }
  if (rshForm && rshInput) {
    rshForm.addEventListener('submit', (e) => {
      e.preventDefault();
      if (!rshActiveAgentId) return;
      const cmd = rshInput.value;
      if (cmd.trim()) {
        rshHistory.push(cmd);
        if (rshHistory.length > 80) rshHistory.shift();
      }
      rshHistIdx = rshHistory.length;
      if (rshActiveIsMac === false) appendTerminalOutput(`> ${cmd}\n`);
      sendWsJson({ type: 'shell_input', agent_id: rshActiveAgentId, data: cmd + '\n' });
      rshInput.value = '';
    });
    rshInput.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowUp' && rshHistory.length > 0) {
        e.preventDefault();
        rshHistIdx = Math.max(0, rshHistIdx - 1);
        rshInput.value = rshHistory[rshHistIdx] || '';
      } else if (e.key === 'ArrowDown' && rshHistory.length > 0) {
        e.preventDefault();
        rshHistIdx = Math.min(rshHistory.length, rshHistIdx + 1);
        rshInput.value = rshHistIdx < rshHistory.length ? rshHistory[rshHistIdx] : '';
      } else if (e.ctrlKey && e.key.toLowerCase() === 'c' && !rshInput.value && rshActiveAgentId) {
        e.preventDefault();
        sendWsJson({ type: 'shell_interrupt', agent_id: rshActiveAgentId });
      } else if (e.key === 'Escape') {
        closeReverseTerminal();
      }
    });
  }
  document.querySelectorAll('.js-rsh-quick').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (!rshActiveAgentId) return;
      const cmd = rshActiveIsMac ? btn.dataset.cmdMac : btn.dataset.cmdWin;
      if (cmd) {
        if (!rshActiveIsMac) appendTerminalOutput(`> ${cmd}\n`);
        sendWsJson({ type: 'shell_input', agent_id: rshActiveAgentId, data: cmd + '\n' });
        if (rshInput) rshInput.focus();
      }
    });
  });

  // =====================================================================
  // EXPLORADOR DE ARCHIVOS ESTILO FINDER SOBRE WSS (MF-FS06 / MF-FS07)
  // =====================================================================
  const finderModal = document.getElementById('finder-modal');
  const fndDeviceTitle = document.getElementById('fnd-device-title');
  const fndSubtitle = document.getElementById('fnd-subtitle');
  const fndFavsList = document.getElementById('fnd-favs-list');
  const fndDrivesList = document.getElementById('fnd-drives-list');
  const fndHiddenCheck = document.getElementById('fnd-hidden-check');
  const fndUpBtn = document.getElementById('fnd-up-btn');
  const fndBreadcrumbs = document.getElementById('fnd-breadcrumbs');
  const fndFilterInput = document.getElementById('fnd-filter-input');
  const fndRefreshBtn = document.getElementById('fnd-refresh-btn');
  const fndRows = document.getElementById('fnd-rows');
  const fndCountLabel = document.getElementById('fnd-count-label');
  const fndStatusPill = document.getElementById('fnd-status-pill');
  const fndCloseBtn = document.getElementById('fnd-close-btn');
  const fndSwitchTermBtn = document.getElementById('fnd-switch-term-btn');
  const rshSwitchFinderBtn = document.getElementById('rsh-switch-finder-btn');
  const fndNewFolderBtn = document.getElementById('fnd-new-folder-btn');
  const fndUploadInput = document.getElementById('fnd-upload-input');
  const fndDropzone = document.getElementById('fnd-dropzone');
  const fndDropOverlay = document.getElementById('fnd-drop-overlay');
  const fndInlineForm = document.getElementById('fnd-inline-form');
  const fndInlineLabel = document.getElementById('fnd-inline-label');
  const fndInlineInput = document.getElementById('fnd-inline-input');
  const fndInlineCancel = document.getElementById('fnd-inline-cancel');
  const fndPreviewDrawer = document.getElementById('fnd-preview-drawer');
  const fndPreviewTitle = document.getElementById('fnd-preview-title');
  const fndPreviewBody = document.getElementById('fnd-preview-body');
  const fndPreviewClose = document.getElementById('fnd-preview-close');

  let fndAgentId = null;
  let fndCwd = '';
  let fndParent = '';
  let fndEntries = [];
  let fndPendingDeletePath = '';
  let fndInlineAction = null;

  function fmtBytes(b) {
    const n = Number(b || 0);
    if (n < 1024) return `${n} B`;
    if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
    if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
    return `${(n / 1073741824).toFixed(2)} GB`;
  }

  function fmtMtime(epochSec) {
    if (!epochSec) return '---';
    const d = new Date(epochSec * 1000);
    return d.toLocaleDateString('es-MX', { day: '2-digit', month: 'short', year: 'numeric' });
  }

  function isPreviewableExt(name) {
    return /\.(png|jpe?g|webp|gif|svg|txt|log|md|json|csv|ini|ya?ml|py|js|bat|sh|xml)$/i.test(name || '');
  }

  function getFileIconSvg(name, isDir) {
    if (isDir) {
      return '<svg width="18" height="18" viewBox="0 0 24 24" fill="#3b82f6" stroke="#2563eb" stroke-width="1.4"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>';
    }
    const low = (name || '').toLowerCase();
    let col = '#64748b';
    if (/\.(xlsx?|csv)$/.test(low)) col = '#10b981';
    else if (/\.(pdf|docx?|pptx?)$/.test(low)) col = '#124fae';
    else if (/\.(png|jpe?g|webp|gif|svg|ai|psd)$/.test(low)) col = '#f87b02';
    else if (/\.(zip|rar|7z|tar|gz)$/.test(low)) col = '#8b5cf6';
    return `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="${col}" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>`;
  }

  function setFinderStatus(text, isErr = false) {
    if (!fndStatusPill) return;
    if (!text) {
      fndStatusPill.classList.add('hidden');
      return;
    }
    fndStatusPill.textContent = text;
    fndStatusPill.className = 'fnd-status-pill' + (isErr ? ' fnd-status-err' : '');
  }

  function requestFinderDir(targetPath) {
    if (!fndAgentId) return;
    fndPendingDeletePath = '';
    if (fndPreviewDrawer) fndPreviewDrawer.classList.add('hidden');
    setFinderStatus('Cargando carpeta...');
    sendWsJson({
      type: 'fs_list',
      agent_id: fndAgentId,
      path: targetPath || '',
      show_hidden: Boolean(fndHiddenCheck && fndHiddenCheck.checked),
    });
  }

  function openFinderExplorer(agentId) {
    if (!finderModal || !agentId) return;
    const ag = state.agents.get(agentId);
    if (!ag) return;
    fndAgentId = agentId;
    if (fndDeviceTitle) fndDeviceTitle.textContent = `Archivos — ${ag.alias || ag.hostname}`;
    if (fndSubtitle) fndSubtitle.textContent = `${ag.hostname} · ${ag.username}`;
    if (fndFilterInput) fndFilterInput.value = '';
    if (fndInlineForm) fndInlineForm.classList.add('hidden');
    finderModal.classList.remove('hidden');
    requestFinderDir('');
  }

  function renderFinderBreadcrumbs(cwd) {
    if (!fndBreadcrumbs) return;
    const isWin = /^[A-Za-z]:\\/.test(cwd || '');
    const sep = isWin ? '\\' : '/';
    const parts = (cwd || '').split(sep).filter(Boolean);
    const crumbs = [];
    if (isWin && parts.length > 0) {
      let acc = parts[0] + '\\';
      crumbs.push({ label: parts[0], path: acc });
      for (let i = 1; i < parts.length; i++) {
        acc = acc.endsWith('\\') ? acc + parts[i] : acc + '\\' + parts[i];
        crumbs.push({ label: parts[i], path: acc });
      }
    } else {
      crumbs.push({ label: 'Raiz (/)', path: '/' });
      let acc = '';
      parts.forEach((p) => {
        acc += '/' + p;
        crumbs.push({ label: p, path: acc });
      });
    }
    fndBreadcrumbs.innerHTML = crumbs
      .map((c, idx) => `${idx > 0 ? '<span class="fnd-crumb-sep">/</span>' : ''}<button type="button" class="fnd-crumb" data-path="${escapeHtml(c.path)}">${escapeHtml(c.label)}</button>`)
      .join('');
    fndBreadcrumbs.scrollLeft = fndBreadcrumbs.scrollWidth;
  }

  function renderFinderRows() {
    if (!fndRows) return;
    const q = ((fndFilterInput && fndFilterInput.value) || '').trim().toLowerCase();
    const list = q ? fndEntries.filter((it) => it.name.toLowerCase().includes(q)) : fndEntries;
    if (fndCountLabel) fndCountLabel.textContent = `${list.length} elemento${list.length === 1 ? '' : 's'} · ${fndCwd}`;
    if (list.length === 0) {
      fndRows.innerHTML = '<div class="fnd-empty">Esta carpeta esta vacia o no hay coincidencias con el filtro.</div>';
      return;
    }
    fndRows.innerHTML = list
      .map((it) => {
        const canPrev = !it.is_dir && isPreviewableExt(it.name);
        const isConfirming = fndPendingDeletePath === it.path;
        return `<div class="fnd-row ${it.is_dir ? 'fnd-row-dir' : ''}" data-path="${escapeHtml(it.path)}" data-isdir="${it.is_dir ? '1' : '0'}" data-name="${escapeHtml(it.name)}">
          <div class="fnd-name-cell">
            ${getFileIconSvg(it.name, it.is_dir)}
            <span class="fnd-item-name" title="${escapeHtml(it.name)}">${escapeHtml(it.name)}</span>
          </div>
          <span class="fnd-col-date">${escapeHtml(fmtMtime(it.mtime))}</span>
          <span class="fnd-col-size">${it.is_dir ? 'Carpeta' : escapeHtml(fmtBytes(it.size))}</span>
          <div class="fnd-actions-cell" style="${isConfirming ? 'opacity:1;' : ''}">
            ${
              isConfirming
                ? `<button type="button" class="fnd-confirm-del js-fnd-del-ok">Borrar</button>`
                : `${
                    canPrev
                      ? `<button type="button" class="fnd-act-btn js-fnd-preview" title="Vista rapida"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg></button>`
                      : ''
                  }
                  <button type="button" class="fnd-act-btn js-fnd-download" title="${it.is_dir ? 'Descargar carpeta en .ZIP' : 'Descargar archivo'}"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg></button>
                  <button type="button" class="fnd-act-btn js-fnd-rename" title="Renombrar"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg></button>
                  <button type="button" class="fnd-act-btn fnd-act-del js-fnd-delete" title="Eliminar"><svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg></button>`
            }
          </div>
        </div>`;
      })
      .join('');
  }

  function handleFinderWsMessage(msg) {
    if (msg.agent_id !== fndAgentId) return;
    if (msg.type === 'fs_list_res') {
      fndCwd = msg.cwd || fndCwd;
      fndParent = msg.parent || '';
      fndEntries = Array.isArray(msg.entries) ? msg.entries : [];
      if (fndFavsList && Array.isArray(msg.favorites)) {
        fndFavsList.innerHTML = msg.favorites
          .map((f) => `<button type="button" class="fnd-side-btn ${f.path === fndCwd ? 'active' : ''}" data-path="${escapeHtml(f.path)}">${escapeHtml(f.label)}</button>`)
          .join('');
      }
      if (fndDrivesList && Array.isArray(msg.drives)) {
        fndDrivesList.innerHTML = msg.drives
          .map((d) => `<button type="button" class="fnd-side-btn ${d.path === fndCwd ? 'active' : ''}" data-path="${escapeHtml(d.path)}">${escapeHtml(d.label)}</button>`)
          .join('');
      }
      renderFinderBreadcrumbs(fndCwd);
      renderFinderRows();
      setFinderStatus(msg.ok === false ? msg.error || 'Error de acceso' : '', msg.ok === false);
    } else if (msg.type === 'fs_download_res') {
      if (!msg.ok) {
        setFinderStatus(msg.error || 'No se pudo descargar', true);
        return;
      }
      setFinderStatus('');
      if (msg.preview && fndPreviewDrawer && fndPreviewBody) {
        if (fndPreviewTitle) fndPreviewTitle.textContent = msg.filename || 'Vista rapida';
        if (String(msg.mime || '').startsWith('image/')) {
          fndPreviewBody.innerHTML = `<img src="data:${escapeHtml(msg.mime)};base64,${msg.data_b64}" alt="${escapeHtml(msg.filename)}" />`;
        } else {
          try {
            const bytes = Uint8Array.from(atob(msg.data_b64), (c) => c.charCodeAt(0));
            const txt = new TextDecoder('utf-8').decode(bytes);
            fndPreviewBody.innerHTML = `<pre>${escapeHtml(txt.slice(0, 80000))}</pre>`;
          } catch (_) {
            fndPreviewBody.innerHTML = '<pre>No se pudo decodificar la vista previa.</pre>';
          }
        }
        fndPreviewDrawer.classList.remove('hidden');
        return;
      }
      const byteChars = atob(msg.data_b64);
      const arr = new Uint8Array(byteChars.length);
      for (let i = 0; i < byteChars.length; i++) arr[i] = byteChars.charCodeAt(i);
      const blob = new Blob([arr], { type: msg.mime || 'application/octet-stream' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = msg.filename || 'archivo';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
    } else if (msg.type === 'fs_op_res') {
      if (!msg.ok) {
        setFinderStatus(msg.error || 'Operacion rechazada', true);
      } else {
        setFinderStatus('Listo');
        setTimeout(() => setFinderStatus(''), 1800);
        requestFinderDir(msg.cwd || fndCwd);
      }
    }
  }

  function closeFinderExplorer() {
    if (finderModal) finderModal.classList.add('hidden');
    if (fndPreviewDrawer) fndPreviewDrawer.classList.add('hidden');
    fndAgentId = null;
  }

  if (fndCloseBtn) fndCloseBtn.addEventListener('click', closeFinderExplorer);
  if (fndPreviewClose) fndPreviewClose.addEventListener('click', () => fndPreviewDrawer.classList.add('hidden'));
  if (fndSwitchTermBtn) {
    fndSwitchTermBtn.addEventListener('click', () => {
      const id = fndAgentId;
      closeFinderExplorer();
      if (id) openReverseTerminal(id);
    });
  }
  if (rshSwitchFinderBtn) {
    rshSwitchFinderBtn.addEventListener('click', () => {
      const id = rshActiveAgentId;
      closeReverseTerminal();
      if (id) openFinderExplorer(id);
    });
  }
  if (fndUpBtn) fndUpBtn.addEventListener('click', () => { if (fndParent) requestFinderDir(fndParent); });
  if (fndRefreshBtn) fndRefreshBtn.addEventListener('click', () => requestFinderDir(fndCwd));
  if (fndHiddenCheck) fndHiddenCheck.addEventListener('change', () => requestFinderDir(fndCwd));
  if (fndFilterInput) fndFilterInput.addEventListener('input', () => renderFinderRows());

  [fndFavsList, fndDrivesList, fndBreadcrumbs].forEach((box) => {
    if (!box) return;
    box.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-path]');
      if (btn && btn.dataset.path) requestFinderDir(btn.dataset.path);
    });
  });

  if (fndNewFolderBtn && fndInlineForm && fndInlineInput) {
    fndNewFolderBtn.addEventListener('click', () => {
      fndInlineAction = { op: 'mkdir', path: fndCwd };
      if (fndInlineLabel) fndInlineLabel.textContent = 'Nueva carpeta:';
      fndInlineInput.value = '';
      fndInlineForm.classList.remove('hidden');
      fndInlineInput.focus();
    });
  }
  if (fndInlineCancel && fndInlineForm) {
    fndInlineCancel.addEventListener('click', () => fndInlineForm.classList.add('hidden'));
  }
  if (fndInlineForm && fndInlineInput) {
    fndInlineForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const val = fndInlineInput.value.trim();
      if (!val || !fndInlineAction || !fndAgentId) return;
      sendWsJson({ type: 'fs_mutate', agent_id: fndAgentId, op: fndInlineAction.op, path: fndInlineAction.path, new_name: val });
      fndInlineForm.classList.add('hidden');
    });
  }

  if (fndRows) {
    fndRows.addEventListener('click', (e) => {
      const row = e.target.closest('.fnd-row');
      if (!row || !fndAgentId) return;
      const rowPath = row.dataset.path || '';
      const rowName = row.dataset.name || '';
      const isDir = row.dataset.isdir === '1';

      if (e.target.closest('.js-fnd-preview')) {
        setFinderStatus('Abriendo vista rapida...');
        sendWsJson({ type: 'fs_download', agent_id: fndAgentId, path: rowPath, preview: true });
      } else if (e.target.closest('.js-fnd-download')) {
        setFinderStatus(isDir ? 'Comprimiendo carpeta (.zip)...' : 'Preparando descarga...');
        sendWsJson({ type: 'fs_download', agent_id: fndAgentId, path: rowPath, preview: false });
      } else if (e.target.closest('.js-fnd-rename')) {
        fndInlineAction = { op: 'rename', path: rowPath };
        if (fndInlineLabel) fndInlineLabel.textContent = 'Renombrar:';
        if (fndInlineInput) { fndInlineInput.value = rowName; fndInlineForm.classList.remove('hidden'); fndInlineInput.focus(); }
      } else if (e.target.closest('.js-fnd-delete')) {
        fndPendingDeletePath = rowPath;
        renderFinderRows();
      } else if (e.target.closest('.js-fnd-del-ok')) {
        fndPendingDeletePath = '';
        setFinderStatus('Eliminando...');
        sendWsJson({ type: 'fs_mutate', agent_id: fndAgentId, op: 'delete', path: rowPath });
      } else if (isDir && !e.target.closest('.fnd-actions-cell')) {
        requestFinderDir(rowPath);
      }
    });
  }

  function uploadFilesToFinder(fileList) {
    if (!fndAgentId || !fndCwd || !fileList || !fileList.length) return;
    Array.from(fileList).forEach((file) => {
      if (file.size > 16 * 1024 * 1024) {
        setFinderStatus(`"${file.name}" supera el limite de 16 MB`, true);
        return;
      }
      setFinderStatus(`Subiendo ${file.name}...`);
      const reader = new FileReader();
      reader.onload = () => {
        const res = String(reader.result || '');
        const comma = res.indexOf(',');
        const b64 = comma >= 0 ? res.slice(comma + 1) : res;
        sendWsJson({ type: 'fs_upload', agent_id: fndAgentId, dest_dir: fndCwd, filename: file.name, data_b64: b64 });
      };
      reader.readAsDataURL(file);
    });
  }

  if (fndUploadInput) {
    fndUploadInput.addEventListener('change', () => {
      uploadFilesToFinder(fndUploadInput.files);
      fndUploadInput.value = '';
    });
  }
  if (fndDropzone && fndDropOverlay) {
    fndDropzone.addEventListener('dragover', (e) => { e.preventDefault(); fndDropOverlay.classList.remove('hidden'); });
    fndDropzone.addEventListener('dragleave', (e) => {
      if (!fndDropzone.contains(e.relatedTarget)) fndDropOverlay.classList.add('hidden');
    });
    fndDropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      fndDropOverlay.classList.add('hidden');
      if (e.dataTransfer && e.dataTransfer.files) uploadFilesToFinder(e.dataTransfer.files);
    });
  }
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && finderModal && !finderModal.classList.contains('hidden')) {
      if (fndInlineForm && !fndInlineForm.classList.contains('hidden')) fndInlineForm.classList.add('hidden');
      else if (fndPreviewDrawer && !fndPreviewDrawer.classList.contains('hidden')) fndPreviewDrawer.classList.add('hidden');
      else closeFinderExplorer();
    }
  });

  // =====================================================================
  // CONSOLA REPRODUCTORA DVR / NVR EN BITACORA (MF-DVR05)
  // =====================================================================
  const dvrModeToggle = document.getElementById('dvr-mode-toggle');
  const dvrConsoleWrap = document.getElementById('dvr-console-wrap');
  const dvrClassicListWrap = document.getElementById('dvr-classic-list-wrap');
  const dvrDateInput = document.getElementById('dvr-date-input');
  const dvrPrevDayBtn = document.getElementById('dvr-prev-day');
  const dvrNextDayBtn = document.getElementById('dvr-next-day');
  const dvrQuickDates = document.getElementById('dvr-quick-dates');
  const dvrOsdState = document.getElementById('dvr-osd-state');
  const dvrOsdDevice = document.getElementById('dvr-osd-device');
  const dvrOsdWin = document.getElementById('dvr-osd-win');
  const dvrOsdClock = document.getElementById('dvr-osd-clock');
  const dvrMainImg = document.getElementById('dvr-main-img');
  const dvrEmptyOverlay = document.getElementById('dvr-empty-overlay');
  const dvrEmptyTitle = document.getElementById('dvr-empty-title');
  const dvrEmptySub = document.getElementById('dvr-empty-sub');
  const dvrPrevCapBtn = document.getElementById('dvr-prev-cap-btn');
  const dvrPlayBtn = document.getElementById('dvr-play-btn');
  const dvrPlayLabel = document.getElementById('dvr-play-label');
  const dvrNextCapBtn = document.getElementById('dvr-next-cap-btn');
  const dvrTramoBadge = document.getElementById('dvr-tramo-badge');
  const dvrFilmstrip = document.getElementById('dvr-filmstrip');
  const dvrSideEvents = document.getElementById('dvr-side-events');
  const dvrTramoCount = document.getElementById('dvr-tramo-count');
  const dvrWindowGroup = document.getElementById('dvr-window-group');
  const dvrTrackWrap = document.getElementById('dvr-track-wrap');
  const dvrHoverTip = document.getElementById('dvr-Hover-tip');
  const dvrCapturesTicks = document.getElementById('dvr-captures-ticks');
  const dvrSlotsBar = document.getElementById('dvr-slots-bar');
  const dvrLens = document.getElementById('dvr-lens');
  const dvrStepTramoPrev = document.getElementById('dvr-step-tramo-prev');
  const dvrStepTramoNext = document.getElementById('dvr-step-tramo-next');
  const dvrScopeToggle = document.getElementById('dvr-scope-toggle');
  const dvrPrevEvBtn = document.getElementById('dvr-prev-ev-btn');
  const dvrNextEvBtn = document.getElementById('dvr-next-ev-btn');
  const dvrEvPosLabel = document.getElementById('dvr-ev-pos-label');
  const dvrHdBtn = document.getElementById('dvr-hd-btn');
  const dvrExpandBtn = document.getElementById('dvr-expand-btn');
  const dvrCapOriginBadge = document.getElementById('dvr-cap-origin-badge');
  const dvrStagePrev = document.getElementById('dvr-stage-prev');
  const dvrStageNext = document.getElementById('dvr-stage-next');
  const dvrDetailCard = document.getElementById('dvr-detail-card');

  let dvrDate = todayIso;
  let dvrToday = todayIso;
  let dvrSlots = [];
  let dvrEvents = [];
  let dvrWindowMins = 15;
  let dvrCenterMin = 720;
  let dvrSelectedEvId = null;
  let dvrPlayTimer = null;
  let dvrCurrentEvObj = null;
  let dvrScope = 'tramo';
  let dvrMainIsHd = false;
  if (dvrDateInput) dvrDateInput.value = dvrDate;

  function minToHhMm(m) {
    const cl = Math.max(0, Math.min(1440, Math.round(m)));
    const hh = String(Math.floor(cl / 60)).padStart(2, '0');
    const mm = String(cl % 60).padStart(2, '0');
    return `${hh}:${mm}`;
  }

  function authedMediaUrl(p, isPreview = false) {
    return buildCaptureUrl(p, isPreview);
  }

  function parseEvMeta(ev) {
    let m = null;
    if (ev && ev.meta_json) {
      try { m = typeof ev.meta_json === 'string' ? JSON.parse(ev.meta_json) : ev.meta_json; } catch (_) {}
    }
    const cap = (ev && ev.evidence_path) || (m && m.evidence_path) || '';
    return { meta: m, cap };
  }

  function resolveDvrCaptureForEvent(ev) {
    if (!ev) return { cap: '', exact: false, capTime: '' };
    const own = parseEvMeta(ev).cap;
    if (own) return { cap: own, exact: true, capTime: String(ev.timestamp || '').slice(11, 19) };
    const idx = dvrEvents.findIndex((x) => String(x.id) === String(ev.id));
    const end = idx >= 0 ? idx - 1 : dvrEvents.length - 1;
    for (let i = end; i >= 0; i--) {
      const cand = dvrEvents[i];
      if (cand.agent_id !== ev.agent_id) continue;
      const cCap = parseEvMeta(cand).cap;
      if (cCap) return { cap: cCap, exact: false, capTime: String(cand.timestamp || '').slice(11, 19) };
    }
    return { cap: '', exact: false, capTime: '' };
  }

  function renderDvrDetailCard(ev, resolved) {
    if (!dvrDetailCard) return;
    if (!ev) {
      dvrDetailCard.innerHTML = '<div class="muted-text" style="font-size:12.4px;">Sin evento seleccionado en este tramo horario. Desliza la barra superior o cambia a "Todo el día".</div>';
      return;
    }
    const { meta } = parseEvMeta(ev);
    const cat = ev.category || 'WINDOW';
    const activeWin = (meta && meta.active_window) || 'Escritorio';
    const prevWin = (meta && meta.previous_window) || '';
    const dur = meta && meta.duration_seconds !== undefined ? meta.duration_seconds : (ev.duration_seconds || 0);
    const capStatus = resolved.cap ? (resolved.exact ? 'Captura exacta' : `Captura previa (${resolved.capTime})`) : 'Sin captura';
    let bodyHtml = `<div class="dvr-detail-body">${formatSpecialKeysHtml(ev.message || '')}${cat === 'NETWORK' ? buildCorrelationHtml(meta) : ''}</div>`;
    if (cat === 'KEYBOARD') {
      const parsed = analyzeKeyboardTranscript((meta && meta.text_content) || '');
      const cnt = (meta && meta.keystrokes_count) || dur || 0;
      bodyHtml = `<div class="dvr-detail-body">
        ${parsed.cleanText ? `<div class="log-kb-clean-text" style="margin-bottom:6px;">${escapeHtml(parsed.cleanText)}</div>` : ''}
        ${parsed.sequenceHtml ? `<details class="log-kb-details" ${!parsed.cleanText ? 'open' : ''}><summary>Secuencia de teclas (${escapeHtml(String(cnt))})</summary><div class="log-kb-seq">${parsed.sequenceHtml}</div></details>` : ''}
        ${buildCorrelationHtml(meta)}
      </div>`;
    }
    dvrDetailCard.innerHTML = `
      <div class="dvr-detail-head">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;">
          <span class="cat-badge cat-${escapeHtml(cat)}">${escapeHtml(cat)}</span>
          <strong style="color:#133447;font-size:13px;">${escapeHtml(ev.alias || 'Equipo')}</strong>
          <span class="muted-text" style="font-size:11.8px;">${escapeHtml(ev.hostname || '')} · ${escapeHtml(ev.timestamp || '')}</span>
        </div>
        <button type="button" class="ev-step-btn js-dvr-open-modal">Ver en grande</button>
      </div>
      <div class="dvr-detail-meta">
        <div><span>Ventana activa</span><strong>${escapeHtml(activeWin)}</strong></div>
        <div><span>${prevWin ? 'Ventana previa' : 'Duración'}</span><strong>${escapeHtml(prevWin || `${dur}s`)}</strong></div>
        <div><span>Evidencia visual</span><strong>${escapeHtml(capStatus)}</strong></div>
      </div>
      ${bodyHtml}`;
  }

  async function fetchDvrDayFromServer(keepScrub = false) {
    if (!dvrSlotsBar) return;
    const dev = (logDeviceFilter && logDeviceFilter.value) || 'ALL';
    const cat = (logCategoryFilter && logCategoryFilter.value) || 'ALL';
    const q = (logSearchInput && logSearchInput.value.trim()) || '';
    const dt = (dvrDateInput && dvrDateInput.value) || dvrDate || todayIso;
    const params = new URLSearchParams({ agent_id: dev, date: dt, category: cat, q });
    if (state.tenantFilter && state.tenantFilter !== 'ALL') params.set('tenant_id', state.tenantFilter);
    try {
      const res = await authFetch(`/api/dvr-day?${params.toString()}`);
      const data = await res.json();
      if (!data || !data.ok) return;
      dvrDate = data.date || dt;
      dvrToday = data.today || todayIso;
      if (dvrDateInput) dvrDateInput.value = dvrDate;
      dvrSlots = Array.isArray(data.slots) ? data.slots : [];
      dvrEvents = Array.isArray(data.events) ? data.events : [];
      renderDvrQuickDates(data.available_dates || []);
      renderDvrTimelineTrack();
      if (!keepScrub) {
        if (dvrEvents.length > 0) {
          const lastEv = dvrEvents[dvrEvents.length - 1];
          dvrCenterMin = Math.floor((lastEv.sec_of_day || 0) / 60);
          dvrSelectedEvId = lastEv.id;
        } else {
          dvrCenterMin = Number(data.now_minute || 720);
          dvrSelectedEvId = null;
        }
      }
      syncDvrPlaybackStage();
    } catch (err) {
      console.error('Error al cargar dia DVR:', err);
    }
  }

  function pushLiveLogToDvr(rawLog) {
    if (!rawLog || dvrDate !== dvrToday) return;
    const ts = String(rawLog.timestamp || '');
    if (!ts.startsWith(dvrDate)) return;
    const h = Number(ts.slice(11, 13) || 0), m = Number(ts.slice(14, 16) || 0), s = Number(ts.slice(17, 19) || 0);
    const sec = h * 3600 + m * 60 + s;
    const sl = Math.max(0, Math.min(287, Math.floor(sec / 300)));
    const enriched = { ...rawLog, sec_of_day: sec, slot_5m: sl };
    dvrEvents.push(enriched);
    if (dvrSlots[sl]) {
      dvrSlots[sl].state = 'active';
      dvrSlots[sl].events = (dvrSlots[sl].events || 0) + 1;
      if (parseEvMeta(enriched).cap) dvrSlots[sl].captures = (dvrSlots[sl].captures || 0) + 1;
    }
    const evMin = Math.floor(sec / 60);
    if (!dvrPlayTimer && Math.abs(evMin - dvrCenterMin) <= 30) {
      dvrCenterMin = evMin;
      dvrSelectedEvId = enriched.id;
    }
    renderDvrTimelineTrack();
    syncDvrPlaybackStage();
  }

  function renderDvrQuickDates(availDates) {
    if (!dvrQuickDates) return;
    dvrQuickDates.innerHTML = availDates.slice(0, 6).map((dt) => {
      const lbl = dt === dvrToday ? 'Hoy' : dt.slice(5);
      return `<button type="button" class="dvr-qdate-btn ${dt === dvrDate ? 'active' : ''}" data-date="${escapeHtml(dt)}">${escapeHtml(lbl)}</button>`;
    }).join('');
  }

  function renderDvrTimelineTrack() {
    if (!dvrSlotsBar) return;
    dvrSlotsBar.innerHTML = dvrSlots.map((s) => `<span class="dvr-slot dvr-slot-${escapeHtml(s.state || 'offline')}"></span>`).join('');
    if (dvrCapturesTicks) {
      const capEvs = dvrEvents.filter((ev) => Boolean(parseEvMeta(ev).cap));
      dvrCapturesTicks.innerHTML = capEvs.map((ev) => {
        const pct = ((ev.sec_of_day || 0) / 86400) * 100;
        return `<span class="dvr-cap-tick" style="left:${pct.toFixed(2)}%;"></span>`;
      }).join('');
    }
  }

  function syncDvrPlaybackStage() {
    const half = dvrWindowMins / 2;
    const startMin = Math.max(0, Math.min(1440 - dvrWindowMins, dvrCenterMin - half));
    const endMin = Math.min(1440, startMin + dvrWindowMins);
    const startSec = startMin * 60;
    const endSec = endMin * 60;

    if (dvrLens) {
      dvrLens.style.left = `${(startMin / 1440) * 100}%`;
      dvrLens.style.width = `${Math.max(0.7, (dvrWindowMins / 1440) * 100)}%`;
    }
    if (dvrTramoBadge) {
      dvrTramoBadge.textContent = `Tramo ${dvrWindowMins}m: ${minToHhMm(startMin)} - ${minToHhMm(endMin)}`;
    }

    const tramoEvs = dvrEvents.filter((ev) => ev.sec_of_day >= startSec && ev.sec_of_day <= endSec);
    const listToShow = dvrScope === 'day' ? dvrEvents : tramoEvs;
    if (dvrTramoCount) dvrTramoCount.textContent = String(listToShow.length);

    const centerSlotIdx = Math.max(0, Math.min(287, Math.floor(dvrCenterMin / 5)));
    const slotInfo = dvrSlots[centerSlotIdx] || { state: 'offline' };

    let activeEv = dvrEvents.find((e) => String(e.id) === String(dvrSelectedEvId));
    if (activeEv && dvrScope === 'tramo' && (activeEv.sec_of_day < startSec || activeEv.sec_of_day > endSec)) {
      activeEv = null;
    }
    if (!activeEv && tramoEvs.length > 0) {
      const withCap = tramoEvs.filter((e) => Boolean(parseEvMeta(e).cap));
      activeEv = withCap.length > 0 ? withCap[withCap.length - 1] : tramoEvs[tramoEvs.length - 1];
      dvrSelectedEvId = activeEv.id;
    }
    dvrCurrentEvObj = activeEv || null;

    const globalIdx = activeEv ? dvrEvents.findIndex((e) => String(e.id) === String(activeEv.id)) : -1;
    if (dvrEvPosLabel) {
      dvrEvPosLabel.textContent = globalIdx >= 0 ? `${globalIdx + 1} / ${dvrEvents.length}` : `0 / ${dvrEvents.length}`;
    }
    [dvrPrevEvBtn, dvrStagePrev].forEach((b) => { if (b) b.disabled = dvrEvents.length === 0 || globalIdx <= 0; });
    [dvrNextEvBtn, dvrStageNext].forEach((b) => { if (b) b.disabled = dvrEvents.length === 0 || (globalIdx >= 0 && globalIdx >= dvrEvents.length - 1); });

    if (dvrOsdState) {
      if (slotInfo.state === 'active' || (activeEv && ['WINDOW', 'KEYBOARD', 'RESUME', 'AUDIT'].includes(activeEv.category))) {
        dvrOsdState.textContent = 'ACTIVO';
        dvrOsdState.className = 'dvr-state-pill dvr-state-active';
      } else if (slotInfo.state === 'idle') {
        dvrOsdState.textContent = 'INACTIVO';
        dvrOsdState.className = 'dvr-state-pill dvr-state-idle';
      } else {
        dvrOsdState.textContent = 'SIN CONEXIÓN';
        dvrOsdState.className = 'dvr-state-pill dvr-state-offline';
      }
    }

    const resolvedCap = resolveDvrCaptureForEvent(activeEv);
    renderDvrDetailCard(activeEv, resolvedCap);

    if (activeEv) {
      const { meta } = parseEvMeta(activeEv);
      const winLbl = (meta && (meta.active_window || meta.app_name)) || activeEv.message || 'Evento';
      if (dvrOsdDevice) dvrOsdDevice.textContent = activeEv.alias || 'Equipo';
      if (dvrOsdWin) dvrOsdWin.textContent = winLbl;
      if (dvrOsdClock) dvrOsdClock.textContent = String(activeEv.timestamp || '').slice(11, 19) || minToHhMm(dvrCenterMin);

      if (resolvedCap.cap && dvrMainImg && dvrEmptyOverlay) {
        dvrMainImg.src = authedMediaUrl(resolvedCap.cap, !dvrMainIsHd);
        dvrMainImg.classList.remove('hidden');
        dvrEmptyOverlay.classList.add('hidden');
        if (dvrCapOriginBadge) {
          dvrCapOriginBadge.classList.remove('hidden');
          dvrCapOriginBadge.classList.toggle('is-inherited', !resolvedCap.exact);
          const baseOrigin = resolvedCap.exact ? 'Captura del evento' : `Última captura previa · ${resolvedCap.capTime}`;
          dvrCapOriginBadge.textContent = `${baseOrigin} · ${dvrMainIsHd ? 'HD' : 'Vista ligera'}`;
        }
      } else if (dvrMainImg && dvrEmptyOverlay) {
        dvrMainImg.classList.add('hidden');
        dvrEmptyOverlay.classList.remove('hidden');
        dvrEmptyOverlay.classList.remove('is-dead');
        if (dvrCapOriginBadge) dvrCapOriginBadge.classList.add('hidden');
        if (dvrEmptyTitle) dvrEmptyTitle.textContent = `${activeEv.category}: ${activeEv.alias}`;
        if (dvrEmptySub) dvrEmptySub.textContent = activeEv.message || 'Evento registrado antes de la primera captura del día.';
      }
    } else if (dvrMainImg && dvrEmptyOverlay) {
      dvrMainImg.classList.add('hidden');
      dvrEmptyOverlay.classList.remove('hidden');
      if (dvrCapOriginBadge) dvrCapOriginBadge.classList.add('hidden');
      if (dvrOsdClock) dvrOsdClock.textContent = `${minToHhMm(startMin)} - ${minToHhMm(endMin)}`;
      if (dvrOsdWin) dvrOsdWin.textContent = slotInfo.state === 'future' ? 'Horario futuro' : 'Sin registros en este tramo';
      const isDead = slotInfo.state === 'offline';
      dvrEmptyOverlay.classList.toggle('is-dead', isDead);
      if (dvrEmptyTitle) {
        dvrEmptyTitle.textContent = slotInfo.state === 'future' ? 'Tramo aún no transcurrido' : (isDead ? 'Tiempo muerto — Sin conexión con el equipo' : 'Equipo conectado sin eventos en este tramo');
      }
      if (dvrEmptySub) {
        dvrEmptySub.textContent = 'Desliza la línea de tiempo superior hacia las zonas verdes o usa las flechas Evento / Captura.';
      }
    }

    if (dvrFilmstrip) {
      const capsInTramo = tramoEvs.filter((e) => Boolean(parseEvMeta(e).cap));
      const pool = capsInTramo.length > 0 ? capsInTramo : dvrEvents.filter((e) => Boolean(parseEvMeta(e).cap)).slice(-14);
      dvrFilmstrip.innerHTML = pool.map((ev) => {
        const { cap } = parseEvMeta(ev);
        return `<button type="button" class="dvr-film-item ${String(ev.id) === String(dvrSelectedEvId) ? 'active' : ''}" data-evid="${ev.id}" title="${escapeHtml(String(ev.timestamp || '').slice(11, 19))} · ${escapeHtml(ev.alias)}"><img src="${escapeHtml(authedMediaUrl(cap, true))}" alt="Miniatura" loading="lazy" /></button>`;
      }).join('');
      const actFilm = dvrFilmstrip.querySelector('.dvr-film-item.active');
      if (actFilm) actFilm.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }

    if (dvrSideEvents) {
      if (listToShow.length === 0) {
        dvrSideEvents.innerHTML = `<div class="logs-empty-feed" style="padding:28px 12px;">Sin eventos entre ${minToHhMm(startMin)} y ${minToHhMm(endMin)}.</div>`;
      } else {
        dvrSideEvents.innerHTML = listToShow.slice(-300).reverse().map((ev) => {
          const { meta, cap } = parseEvMeta(ev);
          const hhmmss = String(ev.timestamp || '').slice(11, 19);
          let summary = ev.message || '';
          if (ev.category === 'KEYBOARD' && meta && meta.text_content) {
            const parsed = analyzeKeyboardTranscript(meta.text_content);
            summary = parsed.cleanText || summary;
          }
          const thumbEl = cap ? `<div class="dvr-ev-mini-thumb"><img src="${escapeHtml(authedMediaUrl(cap, true))}" alt="Cap" loading="lazy" /></div>` : '';
          return `<div class="dvr-ev-card ${String(ev.id) === String(dvrSelectedEvId) ? 'active' : ''}" data-evid="${ev.id}">
            <div class="dvr-ev-body">
              <div class="dvr-ev-top">
                <span class="dvr-ev-time">${escapeHtml(hhmmss)} · ${escapeHtml(ev.alias)}</span>
                <span class="cat-badge cat-${escapeHtml(ev.category || 'WINDOW')}">${escapeHtml(ev.category || 'WINDOW')}</span>
              </div>
              <div class="dvr-ev-msg">${escapeHtml(summary)}</div>
            </div>
            ${thumbEl}
          </div>`;
        }).join('');
        const actCard = dvrSideEvents.querySelector('.dvr-ev-card.active');
        if (actCard) actCard.scrollIntoView({ block: 'nearest' });
      }
    }
  }

  function stopDvrAutoplay() {
    if (dvrPlayTimer) { clearInterval(dvrPlayTimer); dvrPlayTimer = null; }
    if (dvrPlayBtn) dvrPlayBtn.classList.remove('playing');
    if (dvrPlayLabel) dvrPlayLabel.textContent = 'Reproducir';
  }

  function jumpToDvrEvent(evObj) {
    if (!evObj) return;
    dvrSelectedEvId = evObj.id;
    dvrCenterMin = Math.floor((evObj.sec_of_day || 0) / 60);
    syncDvrPlaybackStage();
  }

  function stepDvrEvent(dir) {
    if (dvrEvents.length === 0) return false;
    let idx = dvrEvents.findIndex((e) => String(e.id) === String(dvrSelectedEvId));
    if (idx < 0) {
      const curSec = dvrCenterMin * 60;
      idx = dir > 0 ? dvrEvents.findIndex((e) => e.sec_of_day >= curSec) : dvrEvents.length - 1;
      if (idx < 0) idx = dvrEvents.length - 1;
    } else {
      idx += dir;
      if (idx < 0 || idx >= dvrEvents.length) return false;
    }
    jumpToDvrEvent(dvrEvents[idx]);
    return true;
  }

  function stepDvrCapture(dir) {
    const caps = dvrEvents.filter((e) => Boolean(parseEvMeta(e).cap));
    const list = caps.length > 0 ? caps : dvrEvents;
    if (list.length === 0) return false;
    let idx = list.findIndex((e) => String(e.id) === String(dvrSelectedEvId));
    if (idx < 0) {
      const curSec = dvrCenterMin * 60;
      idx = dir > 0 ? list.findIndex((e) => e.sec_of_day > curSec) : list.length - 1;
      if (idx < 0) idx = 0;
    } else {
      idx += dir;
      if (idx < 0 || idx >= list.length) return false;
    }
    jumpToDvrEvent(list[idx]);
    return true;
  }

  function openDvrCurrentInModal() {
    if (!dvrCurrentEvObj) return;
    stopDvrAutoplay();
    const idx = dvrEvents.findIndex((e) => String(e.id) === String(dvrCurrentEvObj.id));
    const payload = logRowToEvidencePayload(dvrCurrentEvObj, dvrEvents);
    if (!payload) return;
    openEvidenceModal(payload, {
      list: dvrEvents,
      index: idx >= 0 ? idx : 0,
      onSync: (row) => jumpToDvrEvent(row),
    });
  }

  if (dvrTrackWrap) {
    let dragging = false;
    const scrubAtClientX = (clientX) => {
      const rect = dvrTrackWrap.getBoundingClientRect();
      if (!rect.width) return;
      const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
      dvrCenterMin = Math.round(ratio * 1440);
      dvrSelectedEvId = null;
      syncDvrPlaybackStage();
    };
    dvrTrackWrap.addEventListener('pointerdown', (e) => {
      stopDvrAutoplay();
      dragging = true;
      dvrTrackWrap.setPointerCapture(e.pointerId);
      scrubAtClientX(e.clientX);
    });
    dvrTrackWrap.addEventListener('pointermove', (e) => {
      const rect = dvrTrackWrap.getBoundingClientRect();
      if (rect.width && dvrHoverTip) {
        const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        dvrHoverTip.textContent = minToHhMm(ratio * 1440);
        dvrHoverTip.style.left = `${(ratio * 100).toFixed(2)}%`;
        dvrHoverTip.classList.remove('hidden');
      }
      if (dragging) scrubAtClientX(e.clientX);
    });
    dvrTrackWrap.addEventListener('pointerup', () => { dragging = false; });
    dvrTrackWrap.addEventListener('pointercancel', () => { dragging = false; });
    dvrTrackWrap.addEventListener('pointerleave', () => { if (dvrHoverTip) dvrHoverTip.classList.add('hidden'); });
  }

  if (dvrWindowGroup) {
    dvrWindowGroup.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-win]');
      if (!btn) return;
      dvrWindowMins = Number(btn.dataset.win || 15);
      dvrWindowGroup.querySelectorAll('.st-seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
      syncDvrPlaybackStage();
    });
  }

  if (dvrStepTramoPrev) {
    dvrStepTramoPrev.addEventListener('click', () => {
      stopDvrAutoplay();
      dvrCenterMin = Math.max(0, dvrCenterMin - dvrWindowMins);
      dvrSelectedEvId = null;
      syncDvrPlaybackStage();
    });
  }
  if (dvrStepTramoNext) {
    dvrStepTramoNext.addEventListener('click', () => {
      stopDvrAutoplay();
      dvrCenterMin = Math.min(1440, dvrCenterMin + dvrWindowMins);
      dvrSelectedEvId = null;
      syncDvrPlaybackStage();
    });
  }
  if (dvrScopeToggle) {
    dvrScopeToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-scope]');
      if (!btn) return;
      dvrScope = btn.dataset.scope || 'tramo';
      dvrScopeToggle.querySelectorAll('.st-seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
      syncDvrPlaybackStage();
    });
  }

  [dvrPrevEvBtn, dvrStagePrev].forEach((b) => {
    if (b) b.addEventListener('click', () => { stopDvrAutoplay(); stepDvrEvent(-1); });
  });
  [dvrNextEvBtn, dvrStageNext].forEach((b) => {
    if (b) b.addEventListener('click', () => { stopDvrAutoplay(); stepDvrEvent(1); });
  });
  if (dvrPrevCapBtn) dvrPrevCapBtn.addEventListener('click', () => { stopDvrAutoplay(); stepDvrCapture(-1); });
  if (dvrNextCapBtn) dvrNextCapBtn.addEventListener('click', () => { stopDvrAutoplay(); stepDvrCapture(1); });
  if (dvrPlayBtn) {
    dvrPlayBtn.addEventListener('click', () => {
      if (dvrPlayTimer) { stopDvrAutoplay(); return; }
      dvrPlayBtn.classList.add('playing');
      if (dvrPlayLabel) dvrPlayLabel.textContent = 'Pausar';
      dvrPlayTimer = setInterval(() => {
        if (!stepDvrEvent(1)) stopDvrAutoplay();
      }, 1500);
    });
  }

  [dvrFilmstrip, dvrSideEvents].forEach((box) => {
    if (!box) return;
    box.addEventListener('click', (e) => {
      const el = e.target.closest('[data-evid]');
      if (!el) return;
      stopDvrAutoplay();
      const target = dvrEvents.find((x) => String(x.id) === String(el.dataset.evid));
      if (target) jumpToDvrEvent(target);
    });
  });

  if (dvrMainImg) dvrMainImg.addEventListener('click', openDvrCurrentInModal);
  if (dvrExpandBtn) dvrExpandBtn.addEventListener('click', openDvrCurrentInModal);
  if (dvrHdBtn) {
    dvrHdBtn.addEventListener('click', () => {
      dvrMainIsHd = !dvrMainIsHd;
      dvrHdBtn.classList.toggle('is-hd-on', dvrMainIsHd);
      syncDvrPlaybackStage();
    });
  }
  if (dvrDetailCard) {
    dvrDetailCard.addEventListener('click', (e) => {
      if (e.target.closest('.js-dvr-open-modal')) openDvrCurrentInModal();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (evidenceModal && !evidenceModal.classList.contains('hidden')) return;
    if (!viewLogs || viewLogs.classList.contains('hidden')) return;
    if (dvrConsoleWrap && dvrConsoleWrap.classList.contains('hidden')) return;
    const tag = (document.activeElement && document.activeElement.tagName) || '';
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); stopDvrAutoplay(); stepDvrEvent(-1); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); stopDvrAutoplay(); stepDvrEvent(1); }
  });

  function shiftDvrDateBy(days) {
    if (!dvrDateInput) return;
    const base = new Date(`${dvrDate || todayIso}T12:00:00`);
    base.setDate(base.getDate() + days);
    dvrDateInput.value = base.toISOString().slice(0, 10);
    stopDvrAutoplay();
    fetchDvrDayFromServer(false);
  }
  if (dvrPrevDayBtn) dvrPrevDayBtn.addEventListener('click', () => shiftDvrDateBy(-1));
  if (dvrNextDayBtn) dvrNextDayBtn.addEventListener('click', () => shiftDvrDateBy(1));
  if (dvrDateInput) dvrDateInput.addEventListener('change', () => { stopDvrAutoplay(); fetchDvrDayFromServer(false); });
  if (dvrQuickDates) {
    dvrQuickDates.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-date]');
      if (!btn || !dvrDateInput) return;
      dvrDateInput.value = btn.dataset.date;
      stopDvrAutoplay();
      fetchDvrDayFromServer(false);
    });
  }

  if (dvrModeToggle) {
    dvrModeToggle.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-mode]');
      if (!btn) return;
      const isList = btn.dataset.mode === 'list';
      stopDvrAutoplay();
      dvrModeToggle.querySelectorAll('.st-seg-btn').forEach((b) => b.classList.toggle('active', b === btn));
      if (dvrConsoleWrap) dvrConsoleWrap.classList.toggle('hidden', isList);
      if (dvrClassicListWrap) dvrClassicListWrap.classList.toggle('hidden', !isList);
      if (groupLogsToggleBtn) groupLogsToggleBtn.classList.toggle('hidden', !isList);
    });
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
