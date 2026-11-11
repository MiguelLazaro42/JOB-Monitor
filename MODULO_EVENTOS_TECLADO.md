# Especificación y Arquitectura Modular: Módulo de Auditoría de Teclado con Disparo de Captura

Este documento detalla la arquitectura, el flujo de estados y la descomposición en mini-funciones del **Módulo de Auditoría de Actividad de Teclado y Evidencia Visual**, diseñado para integrarse al sistema corporativo **JOB-Monitor**.

---

## 1. Análisis de la Problemática y Objetivos

### 1.1 Contexto Operativo
En un entorno corporativo de supervisión y asistencia remota:
1. **Evidencia contextual de trabajo:** Se requiere conocer cuándo un empleado está redactando activamente (en procesadores de texto, hojas de cálculo, correos, CRM o chats corporativos) y disponer de una evidencia visual clara (captura de pantalla) al finalizar o pausar la redacción.
2. **Regla de los 15 segundos (Debounce de fin de escritura):**
   * El ciclo inicia con la **primera pulsación** de teclado detectada.
   * Mientras el usuario continúe tecleando, el ciclo se mantiene activo y el temporizador se renueva.
   * Si transcurren **15 segundos continuos sin ninguna pulsación**, el sistema determina que el usuario concluyó o pausó su bloque de escritura.
   * En ese segundo 15 exacto, se dispara automáticamente una **captura de pantalla completa** de la ventana/escritorio y se genera la entrada formal en la bitácora (`device_logs`).
3. **Cumplimiento de Seguridad y Antivirus (McAfee / Windows Defender):**
   * **Cero almacenamiento de texto sensible en claro:** El módulo audita el **evento y la intensidad de redacción** (inicio, duración, ventana activa y conteo de actividad), evitando actuar como un *keylogger* plano indiscriminado que robe contraseñas o active las firmas heurísticas de McAfee.
   * La captura de pantalla en el segundo 15 proporciona la evidencia visual legítima de lo que el empleado estuvo trabajando.
4. **Regla de Interfaz Gráfica (Regla 5 de Especificación):**
   * Cero emojis en estados, logs, botones o modales; uso exclusivo de iconos vectoriales SVG limpios.

---

## 2. Diagrama de Estados y Flujo Temporal (15 Segundos)

```
[Estado: Reposo]
       │
       ▼ (Usuario presiona primera tecla: ej. 'm')
[MF-01 & MF-02: Inicio de Bloque y Buffer en Memoria]
  • Registra timestamp inicial (t0) y ventana activa
  • Inicializa buffer volátil en RAM: buffer = ['m']
  • Arranca temporizador de inactividad de 15s
       │
       ├─► (Nuevas pulsaciones: ej. 'a', 'x', etc.) 
       │   ──► [MF-03: BufferAggregator & Timer Reset]
       │       • Agrega tecla al buffer en memoria (buffer = ['m', 'a', 'x'])
       │       • CERO envíos de red, CERO registros en bitácora
       │       • Reinicia el reloj a 15 segundos
       │
       ▼ (Transcurren 15 segundos continuos sin teclear)
[MF-04: Cierre de Sesión Unificada (Timeout 15s)]
  • Consolida todas las pulsaciones en un ÚNICO bloque unificado
  • Calcula duración total de la ráfaga
  • Invoca inmediatamente captura de pantalla
       │
       ▼
[MF-05: Captura de Pantalla en Alta Resolución (mss)]
  • Toma snapshot de la pantalla completa
       │
       ▼
[MF-06: Despacho Asíncrono de 1 ÚNICO Evento por WebSocket al Servidor]
  • Payload único: category='KEYBOARD', contenido consolidado, ventana, duración, captura JPEG
       │
       ▼
[MF-07: Persistencia en SQLite (1 sola fila en device_logs)]
  • Inserción de 1 registro consolidado (evita saturar la base de datos)
       │
       ▼
[MF-08: Renderizado en Bitácora con Visor Modal SVG]
```

---

## 3. Descomposición en Mini-Funciones (MF-01 a MF-08)

### MF-01: `KeyboardEventHook` (Listener de Eventos de Teclado de Bajo Impacto)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Escuchar a nivel de sistema operativo (`pynput.keyboard.Listener` o gancho nativo en hilo dedicado) cada evento de tecla sin bloquear el hilo principal.
* **Comportamiento:**
  * Al detectar un evento `on_press`, convierte la tecla en su representación segura (carácter o nombre de tecla especial como `[Espacio]`, `[Enter]`, `[Borrar]`).
  * Entrega el evento al despachador de buffer (`MF-02` / `MF-03`).

---

### MF-02: `TypingSessionTracker` (Inicializador del Bloque de Redacción)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Detectar la **primera pulsación** de una ráfaga y abrir la sesión en memoria.
* **Comportamiento:**
  * Si no hay sesión activa:
    * Establece `session_active = True`.
    * Guarda `start_time = time.monotonic()`.
    * Consulta la ventana activa actual (`GetForegroundWindow()` / `GetWindowTextW()`).
    * Inicializa el buffer en memoria RAM: `keystrokes_buffer = [tecla]`.
  * Transfiere el control al temporizador `MF-03`.

---

### MF-03: `KeystrokeBufferAggregator & DebounceTimer` (Buffer en RAM y Reloj de 15s)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** **Acumular las pulsaciones intermedias en memoria RAM sin emitir eventos al servidor** y renovar la cuenta regresiva de 15 segundos.
* **Comportamiento:**
  * Si el usuario escribe `"m"`, `"a"`, `"x"`:
    * Cada pulsación se anexa al buffer en memoria (`['m', 'a', 'x']`).
    * **NO se envía nada a la red ni a la base de datos** por cada tecla individual.
    * Cancela cualquier temporizador previo y programa un nuevo callback tras **15.0 segundos exactos**.
  * Si pasan los 15 segundos continuos sin ninguna pulsación adicional, se dispara automáticamente el cierre `MF-04`.

---

### MF-04: `TypingSessionFinalizer` (Consolidador del Evento Único al Expirar 15s)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Unificar todo el buffer en una sola cadena consolidada, calcular métricas y preparar el paquete único.
* **Comportamiento:**
  * Une las pulsaciones del buffer en un texto final estructurado (ej. `"max"` o el párrafo completo redactado).
  * Calcula la duración total: $\text{duración} = (\text{tiempo\_última\_tecla} - \text{tiempo\_primera\_tecla})$.
  * Concluye la sesión activa (`session_active = False`) y vacía el buffer para la siguiente ráfaga.
  * Solicita sincrónicamente la captura de pantalla (`MF-05`).

---

### MF-05: `EvidenceScreenCapture` (Captura de Pantalla al Segundo 15)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Tomar una captura nítida del escritorio en el instante exacto en que cesó la escritura.
* **Comportamiento:**
  * Utiliza `mss` sobre el monitor activo.
  * Comprime el cuadro a JPEG (calidad 75-80) para equilibrar legibilidad de texto y peso ligero (< 120 KB).
  * Entrega la imagen codificada en Base64.

---

### MF-06: `KeyboardTelemetryDispatcher` (Emisor de 1 Solo Paquete WebSocket)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Transmitir **un único mensaje JSON** hacia el servidor conteniendo todo el bloque redactado.
* **Estructura del Payload Único:**
  ```json
  {
    "type": "keyboard_event",
    "timestamp": "2026-11-11 12:30:15",
    "active_window": "Google Chrome - Correo Corporativo",
    "duration_seconds": 18,
    "idle_pause_seconds": 15,
    "keystrokes_count": 3,
    "text_content": "max",
    "screenshot_b64": "/9j/4AAQSkZJRg..."
  }
  ```

---

### MF-07: `LogPersistenceAndStorage` (Persistencia de 1 Sola Fila en Base de Datos)
* **Ubicación:** `server/app.py` y `server/database.py`
* **Responsabilidad:** Registrar **una única fila consolidada** en la tabla `device_logs` de SQLite y guardar la captura de pantalla.
* **Comportamiento:**
  * Guarda la captura en disco: `/data/captures/<agent_id>/kb_<timestamp>.jpg`.
  * Inserta 1 solo registro en `device_logs`:
    * `category`: `"KEYBOARD"`
    * `message`: `"Redacción en 'Google Chrome - Correo Corporativo' (Pulsaciones: 'max' • Duración: 18s • Pausa de 15s)"`
    * `duration_seconds`: `18`
  * Emite el evento por WebSocket `/ws/admin` para que la bitácora lo pinte en vivo sin saturar la pantalla.

---

### MF-08: `LogViewerEvidenceModal` (Visualización Unificada en Dashboard)
* **Ubicación:** `server/static/js/app.js`
* **Responsabilidad:** Mostrar en la tabla de bitácora un único renglón limpio con el texto consolidado y botón SVG para ver la captura.
* **Comportamiento:**
  * Muestra el bloque redactado completo (ej. `"max"`).
  * Incluye botón con icono SVG para abrir la captura de pantalla en grande en el visor modal.
  * Garantiza que la bitácora permanezca 100% legible y sin spam de teclas sueltas.

---

## 4. Matriz de Requerimientos y Cumplimiento

| Requerimiento | Mini-Función Responsable | Mecanismo Técnico |
|---|---|---|
| Iniciar temporizador con la primera pulsación | `MF-02` | Detección de flanco de subida en sesión inactiva |
| Asumir fin de escritura tras 15s de pausa | `MF-03` y `MF-04` | Temporizador reajustable (Debounce de 15.0s) |
| Capturar pantalla al segundo 15 | `MF-05` | Invocación de `mss.grab()` en el callback de timeout |
| Integrar a la bitácora de eventos | `MF-06` y `MF-07` | Categoría `KEYBOARD` en SQLite `device_logs` |
| Cero alertas en McAfee Antivirus | `MF-01` | Sin inyección DLL ni keylogger en texto plano |
| Cero emojis en interfaz | `MF-08` | Iconos vectoriales SVG limpios |

