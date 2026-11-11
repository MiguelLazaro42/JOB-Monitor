# JOB-Monitor — Monitoreo de Actividad y Asistencia Remota Corporativa

Sistema corporativo en Python de **monitoreo de actividad en tiempo real (estilo Microsoft Teams)** y **asistencia remota silenciosa** (sin cerrar la sesión del usuario en Windows y sin alertas de confirmación).

Toda la arquitectura detallada, esquemas de base de datos, contratos WebSocket y el roadmap de las 4 fases se encuentran en:
- [`ESPECIFICACION_PROYECTO_MONITOREO_REMOTO.md`](./ESPECIFICACION_PROYECTO_MONITOREO_REMOTO.md)

---

## Estado Actual: Fases 1, 2, 3 y 4 Completadas (Desplegado en VPS `192.168.103.72`)

### Servidor VPS en Producción
- **Host / IP del Servidor:** `192.168.103.72` (Alias SSH: `Job-Monitor`)
- **Panel Web Central:** `http://192.168.103.72:5080`
- **Endpoint WebSocket de Agentes:** `ws://192.168.103.72:5080/ws/agent` (con verificación mTLS X.509)
- **Ruta en el VPS:** `/opt/JOB-Monitor` (administrado por `systemd` como servicio `job-monitor.service`)

---

### Fase 1: Núcleo de Conexión Inversa, Visor Multi-Monitor y Control Remoto
- **Servidor Central (`server/app.py`):** FastAPI + WebSockets (`/ws/agent` y `/ws/admin`) con gestión de streaming bajo demanda (0% consumo de ancho de banda continuo cuando nadie observa un equipo).
- **Agente Silencioso (`agent/agent.py`):**
  - Compatible con ejecución limpia vía `pythonw.exe` oficial (firmado digitalmente por la *Python Software Foundation* para evitar bloqueos de McAfee / Windows SmartScreen).
  - Corrección automática de escalado DPI en Windows (`SetProcessDpiAwareness`).
  - Detección automática de múltiples monitores (`mss`) y envío de posición relativa del cursor.
  - Control remoto de mouse y teclado (`pynput`) con atajos rápidos (`Alt+Tab`, `Win+D`, `Ctrl+Shift+Esc`).

### Fase 2: Monitoreo Tipo Teams, Base de Datos SQLite y Bitácora de Logs
- **Persistencia en SQLite (`server/database.py`):**
  - Tablas `devices`, `device_logs`, `window_usage_daily`, `daily_activity_summary` y `chat_messages` con modo `WAL`.
  - Registro automático de inicio de jornada, segundos activos vs. inactivos, alertas de ausencia prolongada (+10 min) y tiempo en foco por aplicación.
- **Detección de Eventos en el Agente y Servidor:**
  - Registro cronológico de cambios de ventana (`WINDOW`) con duración de permanencia en la aplicación anterior.
  - Registro automático de entradas en inactividad (`IDLE`), regresos a actividad (`RESUME`), conexiones/desconexiones (`NETWORK`) y auditoría de soporte (`AUDIT`).
- **Panel de Administrador (4 Vistas con Iconografía 100% SVG, Cero Emojis):**
  1. **Muro de Monitoreo:** Estado en vivo, contador de equipos desconectados, ventana activa y botones directos a `Conectar`, `Chat` y `Logs` por equipo.
  2. **Visor Remoto:** Selector de monitores, calidad/FPS, interruptor **Modo: Solo Observar** vs **Modo: Controlar** y auditoría automática.
  3. **Métricas del Día:** Tabla comparativa por fecha con horas activas/inactivas, alertas y porcentajes de uso por aplicación.
  4. **Logs de Equipos:** Bitácora en tiempo real con filtros por equipo, categoría (`IDLE`, `RESUME`, `WINDOW`, `NETWORK`, `AUDIT`), buscador por texto y exportación a **CSV**.

### Fase 3: Herramientas de Asistencia (Voz Push-to-Talk, Chat Emergente, Portapapeles y Archivos)
- **Voz en Directo Push-to-Talk (`server/static/js/audio.js` + `agent/agent.py`):**
  - Captura de micrófono del administrador desde el navegador empaquetada en bloques `.wav` RIFF PCM 16 kHz y reproducida en tiempo real en la PC mediante `winsound.PlaySound(..., SND_MEMORY)`.
- **Ventana de Chat Emergente Bajo Demanda (`tkinter` `topmost`) + Panel Lateral de Mensajes Directos:**
  - Permanece 100% cerrada e invisible mientras el administrador solo observa.
  - Se abre automáticamente con sonido de notificación únicamente cuando el administrador envía un mensaje directo o usa los **botones rápidos de 1 clic**, permitiendo respuesta bidireccional en vivo y persistencia en SQLite.
- **Portapapeles Bidireccional (`pyperclip` + fallback nativo):**
  - Lectura remota del portapapeles de la PC o inyección de texto desde la Mac con opción de pegado automático (`Ctrl+V`).
- **Transferencia Directa de Archivos (`POST /api/send-file/{agent_id}`):**
  - Envío por clic o *Drag & Drop* hacia el `Escritorio (Desktop)` o `Descargas (Downloads)` del empleado, con opción de abrir/ejecutar automáticamente (`os.startfile`) al terminar.

---

## Inicio Rápido y Conexión al VPS (`192.168.103.72`)

### 1. Acceso al Panel en el VPS (`Job-Monitor`)

- **URL del Panel Web:** `http://192.168.103.72:5080`
- **Acceso SSH al servidor:**
  ```bash
  ssh Job-Monitor
  systemctl status job-monitor
  ```

### 2. Ejecutar en entorno local (Desarrollo)

```bash
bash install.sh
./start.sh
```

### 3. Conectar un Agente apuntando al VPS (`192.168.103.72`)

```bash
.venv/bin/python agent/agent.py --server "ws://192.168.103.72:5080/ws/agent" --alias "PC de Miguel - Dirección"
```
