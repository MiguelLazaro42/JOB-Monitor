# Especificación Técnica: MF-01 (KeyboardEventHook)

Este documento contiene exclusivamente la especificación técnica de la **Mini-Función 01 (MF-01)** del módulo de auditoría de teclado para **JOB-Monitor**.

---

## 1. Identificación y Alcance

* **Identificador:** `MF-01`
* **Nombre:** `EventHook` 
* **Archivo destino:** `agent/agent.py`
* **Dependencias:** `pynput.keyboard` (`Listener`, `Key`, `KeyCode`)
* **Propósito:** Interceptar de forma no invasiva las pulsaciones del teclado en segundo plano y canalizarlas hacia el manejador de eventos del agente

---

## 2. Responsabilidades de MF-01

1. **Captura no bloqueante:**
   * Ejecutar la escucha en un hilo secundario independiente (`daemon=True`) gestionado por `pynput.keyboard.Listener`.
   * El hilo principal de WebSocket y streaming de video jamás debe bloquearse ni sufrir caídas.

2. **Normalización de Teclas:**
   * Mapear cada tecla presionada (`on_press`) a una representación uniforme:
     * Caracteres estándar imprimibles: necesitamos saber el idioma del teclado que esta usando el usuario y usar esa decodificacion para el dashboard
     * Teclas especiales: se normalizan a etiquetas comprensibles (ej. `[Espacio]`, `[Enter]`, `[Borrar]`, `[Tab]`, `[Esc]`).
   * Descartar eventos duplicados de liberación de tecla (`on_release`) para contar exclusivamente pulsaciones efectivas.



3. **Ciclo de Vida Limpio:**
   * Proveer métodos de control: `start()` para iniciar la escucha y `stop()` para liberar el hook del sistema operativo cuando el agente se detenga o reinicie.

---

## 3. Contrato de Interfaz

### 3.1 Inicialización
```python
listener = KeyboardEventHook(on_key_callback=self._handle_raw_key_event)
listener.start()
```

### 3.2 Parámetros del Callback
* **`key_repr` (str):** Cadena representativa de la tecla presionada (ej. `'m'` o `'[Espacio]'`).
* **`timestamp` (float):** Marca de tiempo monótona (`time.monotonic()`) de la pulsación para cálculo de intervalos.
