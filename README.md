# JOB-Monitor — Monitoreo de Actividad y Asistencia Remota Corporativa

Sistema corporativo en Python de **monitoreo de actividad en tiempo real (estilo Microsoft Teams)** y **asistencia remota silenciosa** (sin cerrar la sesión del usuario en Windows y sin alertas de confirmación).

Toda la arquitectura detallada, esquemas de base de datos, contratos WebSocket y el roadmap de las 4 fases se encuentran en:
- [`ESPECIFICACION_PROYECTO_MONITOREO_REMOTO.md`](./ESPECIFICACION_PROYECTO_MONITOREO_REMOTO.md)

---

## Estado Actual: Fases 1 y 2 Completadas

### Fase 1: Núcleo de Conexión Inversa, Visor Multi-Monitor y Control Remoto
- **Servidor Central (`server/app.py`):** FastAPI + WebSockets (`/ws/agent` y `/ws/admin`) con gestión de streaming bajo demanda (0% consumo de ancho de banda continuo cuando nadie observa un equipo).
- **Agente Silencioso (`agent/agent.py`):**
  - Compatible con ejecución limpia vía `pythonw.exe` oficial (firmado digitalmente por la *Python Software Foundation* para evitar bloqueos de McAfee / Windows SmartScreen).
  - Corrección automática de escalado DPI en Windows (`SetProcessDpiAwareness`).
  - Detección automática de múltiples monitores (`mss`) y envío de posición relativa del cursor.
  - Control remoto de mouse y teclado (`pynput`) con atajos rápidos (`Alt+Tab`, `Win+D`, `Ctrl+Shift+Esc`).

### Fase 2: Monitoreo Tipo Teams, Base de Datos SQLite y Bitácora de Logs
- **Persistencia en SQLite (`server/database.py`):**
  - Tablas `devices`, `device_logs`, `window_usage_daily` y `daily_activity_summary` con modo `WAL`.
  - Registro automático de inicio de jornada, segundos activos vs. inactivos, alertas de ausencia prolongada (+10 min) y tiempo en foco por aplicación.
- **Detección de Eventos en el Agente y Servidor:**
  - Registro cronológico de cambios de ventana (`WINDOW`) con duración de permanencia en la aplicación anterior.
  - Registro automático de entradas en inactividad (`IDLE`), regresos a actividad (`RESUME`), conexiones/desconexiones (`NETWORK`) y auditoría de soporte (`AUDIT`).
- **Panel de Administrador (4 Vistas con Iconografía 100% SVG, Cero Emojis):**
  1. **Muro de Monitoreo:** Estado en vivo, contador de equipos desconectados, ventana activa y botón directo a Logs por equipo.
  2. **Visor Remoto:** Selector de monitores, calidad/FPS, interruptor **Modo: Solo Observar** vs **Modo: Controlar** y auditoría automática.
  3. **Métricas del Día:** Tabla comparativa por fecha con horas activas/inactivas, alertas y porcentajes de uso por aplicación.
  4. **Logs de Equipos:** Bitácora en tiempo real con filtros por equipo, categoría (`IDLE`, `RESUME`, `WINDOW`, `NETWORK`, `AUDIT`), buscador por texto y exportación a **CSV**.

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
