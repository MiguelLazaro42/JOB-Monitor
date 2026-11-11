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
       ▼ (Usuario presiona primera tecla)
[MF-01 & MF-02: Inicio de Bloque de Escritura]
  • Registra timestamp inicial (t0) y ventana activa
  • Arranca temporizador de inactividad (15s)
       │
       ├─► (Nueva pulsación antes de 15s) ──► [MF-03: Reset de temporizador a 15s]
       │                                            │
       ▼ (Transcurren 15 segundos sin teclas)       │
[MF-04: Disparo de Fin de Redacción (Timeout 15s)] ◄┘
  • Calcula duración total de la sesión de redacción
  • Invoca inmediatamente captura de pantalla
       │
       ▼
[MF-05: Captura de Pantalla en Alta Resolución (mss)]
  • Toma snapshot de la pantalla completa
       │
       ▼
[MF-06: Despacho Asíncrono por WebSocket al Servidor]
  • Payload: category='KEYBOARD', ventana, duración, imagen JPEG
       │
       ▼
[MF-07: Persistencia en SQLite y Almacenamiento de Evidencia]
  • Inserción en device_logs con ruta/enlace de captura
       │
       ▼
[MF-08: Renderizado en Bitácora con Visor Modal SVG]
```

---

## 3. Descomposición en Mini-Funciones (MF-01 a MF-08)

### MF-01: `KeyboardEventHook` (Listener de Eventos de Teclado de Bajo Impacto)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Escuchar a nivel de sistema operativo (`pynput.keyboard.Listener` o gancho nativo en hilo dedicado) las pulsaciones de teclado sin interferir con la entrada del usuario.
* **Comportamiento:**
  * Al recibir un evento `on_press`, notifica inmediatamente al despachador de estado (`MF-02`).
  * No almacena caracteres específicos ni credenciales en archivos de texto, protegiendo la compatibilidad con McAfee Antivirus.

---

### MF-02: `TypingSessionTracker` (Inicializador del Bloque de Redacción)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Determinar si la pulsación pertenece a una ráfaga existente o si constituye el **inicio de un nuevo bloque de redacción**.
* **Comportamiento:**
  * Si no hay sesión activa:
    * Establece `session_active = True`.
    * Guarda `start_time = time.monotonic()`.
    * Consulta la ventana activa actual mediante `GetForegroundWindow()` / `GetWindowTextW()`.
    * Inicializa el contador de pulsaciones `keystroke_count = 1`.
  * Si la sesión ya estaba activa:
    * Incrementa `keystroke_count += 1`.
    * Notifica a `MF-03` para renovar el temporizador.

---

### MF-03: `InactivityDebounceTimer` (Temporizador Reajustable de 15 Segundos)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Gestionar el reloj de cuenta regresiva de 15 segundos.
* **Comportamiento:**
  * Con cada pulsación recibida, cancela cualquier temporizador anterior y programa un nuevo callback tras **15.0 segundos exactos**.
  * Si transcurren los 15 segundos sin cancelaciones, ejecuta la rutina de cierre `MF-04`.

---

### MF-04: `TypingSessionFinalizer` (Consolidador de Métricas al Expirar 15s)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Declarar formalmente concluido el bloque de redacción y calcular métricas consolidadas.
* **Comportamiento:**
  * Calcula la duración efectiva: $\text{duración} = (\text{timestamp\_última\_tecla} - \text{timestamp\_primera\_tecla})$.
  * Obtiene la ventana activa final para cotejar si cambió de programa durante la redacción.
  * Restablece el estado interno del agente a reposo (`session_active = False`).
  * Desencadena sincrónicamente la captura de pantalla (`MF-05`).

---

### MF-05: `EvidenceScreenCapture` (Captura de Pantalla al Segundo 15)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Tomar una captura nítida del escritorio en el instante exacto en que cesó la escritura.
* **Comportamiento:**
  * Utiliza la instancia de `mss` del monitor primario o activo.
  * Comprime el cuadro a JPEG (calidad optimizada 75-80) para equilibrar nitidez de texto y tamaño de transferencia (< 120 KB).
  * Devuelve los bytes JPEG codificados en Base64 o adjuntos para su transmisión.

---

### MF-06: `KeyboardTelemetryDispatcher` (Emisor de Paquete WebSocket)
* **Ubicación:** `agent/agent.py`
* **Responsabilidad:** Enviar el evento consolidado al Servidor Central mediante el WebSocket `/ws/agent`.
* **Estructura del Payload:**
  ```json
  {
    "type": "keyboard_event",
    "timestamp": "2026-11-11 12:30:15",
    "active_window": "Microsoft Word - Informe Trimestral.docx",
    "duration_seconds": 38,
    "idle_pause_seconds": 15,
    "keystrokes_count": 142,
    "screenshot_b64": "/9j/4AAQSkZJRg..."
  }
  ```

---

### MF-07: `LogPersistenceAndStorage` (Recepción y Guardado en Servidor)
* **Ubicación:** `server/app.py` y `server/database.py`
* **Responsabilidad:** Almacenar la captura y registrar el evento en la base de datos SQLite.
* **Comportamiento:**
  * Guarda la imagen en disco estructurado: `/data/captures/<agent_id>/keyboard_<timestamp>.jpg`.
  * Inserta una fila en `device_logs`:
    * `category`: `"KEYBOARD"`
    * `message`: `"Redacción activa durante 38s en 'Microsoft Word - Informe Trimestral.docx' (142 pulsaciones • Pausa de 15s detectada)"`
    * `duration_seconds`: `38`
    * `evidence_path`: `"captures/<agent_id>/keyboard_<timestamp>.jpg"`
  * Notifica a los administradores conectados vía WebSocket `/ws/admin` con `new_log`.

---

### MF-08: `LogViewerEvidenceModal` (Visualización Interactiva en el Panel Web)
* **Ubicación:** `server/static/js/app.js` y `server/templates/index.html`
* **Responsabilidad:** Desplegar el evento en la Vista 4 (Bitácora) con capacidad de ver la evidencia capturada.
* **Comportamiento:**
  * Muestra la insignia morada/índigo `TECLADO`.
  * Añade un botón con icono vectorial SVG de imagen/ojo para abrir la captura de pantalla a resolución completa en un modal emergente.
  * Permite filtrar en el selector de categoría por `"Eventos de Teclado / Redacción"`.

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
