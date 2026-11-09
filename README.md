# RemoteAssist — Monitoreo de Actividad y Asistencia Remota Corporativa

Sistema corporativo en Python de **monitoreo de actividad en tiempo real (estilo Microsoft Teams)** y **asistencia remota silenciosa** (sin cerrar la sesión del usuario en Windows y sin alertas de confirmación).

Toda la arquitectura detallada, esquemas de base de datos, contratos WebSocket y el roadmap de las 4 fases se encuentran en:
- [`ESPECIFICACION_PROYECTO_MONITOREO_REMOTO.md`](./ESPECIFICACION_PROYECTO_MONITOREO_REMOTO.md)

---

## Estado Actual: Fase 1 Completada (MVP)

- **Servidor Central (`server/app.py`):** FastAPI + WebSockets (`/ws/agent` y `/ws/admin`) con gestión de streaming bajo demanda (0% consumo de ancho de banda continuo cuando nadie observa un equipo) y persistencia de alias por `UUID` de hardware.
- **Agente Silencioso (`agent/agent.py`):**
  - Compatible con ejecución limpia vía `pythonw.exe` oficial (firmado digitalmente por la *Python Software Foundation* para evitar bloqueos de McAfee / Windows SmartScreen).
  - Corrección automática de escalado DPI en Windows (`SetProcessDpiAwareness`).
  - Detección automática de múltiples monitores (`mss`) y envío de posición relativa del cursor.
  - Lectura nativa de segundos de inactividad (`GetLastInputInfo`) y ventana activa (`GetForegroundWindow`).
  - Control remoto de mouse y teclado (`pynput`) con atajos rápidos (`Alt+Tab`, `Win+D`, `Ctrl+Shift+Esc`).
- **Panel de Administrador (`server/templates/index.html`):**
  - Interfaz 100% con iconografía vectorial SVG (cero emojis).
  - **Vista 1 (Muro de Equipos):** Tarjetas en vivo con semáforo de actividad, ventana activa, miniatura y renombrado de alias.
  - **Vista 2 (Visor en Vivo):** Selector de monitores, ajuste de calidad/FPS y botón interruptor entre **Modo: Solo Observar** (por defecto) y **Modo: Controlando Mouse y Teclado**.

---

## Inicio Rápido

### 1. Instalar e iniciar el Servidor Central / Panel

```bash
bash install.sh
./start.sh
```

El panel se abrirá automáticamente en `http://127.0.0.1:5060` (Contraseña por defecto: `admin123`, configurable mediante la variable de entorno `RA_ADMIN_PASS`).

### 2. Conectar un Agente de prueba

```bash
.venv/bin/python agent/agent.py --server "ws://127.0.0.1:5060/ws/agent" --alias "PC de Miguel - Dirección"
```
