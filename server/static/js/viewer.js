class RemoteCanvasViewer {
  constructor(canvasEl, stageEl, sendCommandCallback) {
    this.canvas = canvasEl;
    this.ctx = this.canvas.getContext('2d');
    this.stage = stageEl;
    this.sendCommand = sendCommandCallback;

    this.controlEnabled = false;
    this.activeAgentId = null;
    this.frameCount = 0;
    this.lastFpsCheck = performance.now();
    this.currentFps = 0;

    this.lastMoveSentAt = 0;
    this.isRendering = false;

    this._bindInputEvents();
  }

  setAgent(agentId) {
    this.activeAgentId = agentId;
    this.setControlMode(false); // Siempre inicia en Modo: Solo Observar por seguridad
    this.frameCount = 0;
    this.lastFpsCheck = performance.now();
  }

  setControlMode(enabled) {
    this.controlEnabled = Boolean(enabled);
    this.stage.classList.toggle('control-enabled', this.controlEnabled);

    const btn = document.getElementById('toggle-control-btn');
    const iconObs = document.getElementById('mode-icon-observe');
    const iconCtrl = document.getElementById('mode-icon-control');
    const label = document.getElementById('mode-btn-text');

    if (this.controlEnabled) {
      btn.className = 'btn btn-mode-control';
      iconObs.classList.add('hidden');
      iconCtrl.classList.remove('hidden');
      label.textContent = 'Control Activo';
      this.stage.focus();
    } else {
      btn.className = 'btn btn-mode-observe';
      iconObs.classList.remove('hidden');
      iconCtrl.classList.add('hidden');
      label.textContent = 'Observar';
    }
  }

  toggleControlMode() {
    this.setControlMode(!this.controlEnabled);
  }

  async renderBinaryFrame(arrayBuffer) {
    if (this.isRendering || !arrayBuffer || arrayBuffer.byteLength < 8) {
      return;
    }
    this.isRendering = true;

    try {
      const view = new DataView(arrayBuffer);
      const headerLen = view.getUint32(0, false);
      if (headerLen <= 0 || 4 + headerLen >= arrayBuffer.byteLength) {
        this.isRendering = false;
        return;
      }

      const headerBytes = new Uint8Array(arrayBuffer, 4, headerLen);
      const headerText = new TextDecoder('utf-8').decode(headerBytes);
      const meta = JSON.parse(headerText);

      const jpegBytes = new Uint8Array(arrayBuffer, 4 + headerLen);
      const blob = new Blob([jpegBytes], { type: 'image/jpeg' });
      const bitmap = await createImageBitmap(blob);

      if (this.canvas.width !== bitmap.width || this.canvas.height !== bitmap.height) {
        this.canvas.width = bitmap.width;
        this.canvas.height = bitmap.height;
      }

      this.ctx.drawImage(bitmap, 0, 0);
      bitmap.close();

      // Dibujar indicador del cursor remoto del empleado
      if (typeof meta.cx === 'number' && typeof meta.cy === 'number' && meta.cx >= 0 && meta.cy >= 0) {
        const px = meta.cx * this.canvas.width;
        const py = meta.cy * this.canvas.height;
        this._drawRemoteCursor(px, py);
      }

      // Ocultar overlay de carga al recibir el primer cuadro
      const loadingEl = document.getElementById('viewer-loading');
      if (loadingEl && !loadingEl.classList.contains('hidden')) {
        loadingEl.classList.add('hidden');
      }

      // Actualizar etiqueta de resolución y FPS
      const resLabel = document.getElementById('viewer-resolution-label');
      if (resLabel && meta.w && meta.h) {
        resLabel.textContent = `${meta.w}×${meta.h}`;
      }

      this.frameCount += 1;
      const now = performance.now();
      if (now - this.lastFpsCheck >= 1000) {
        this.currentFps = Math.round((this.frameCount * 1000) / (now - this.lastFpsCheck));
        this.frameCount = 0;
        this.lastFpsCheck = now;
        const fpsEl = document.getElementById('viewer-fps-counter');
        if (fpsEl) {
          fpsEl.textContent = `${this.currentFps} FPS`;
        }
      }
    } catch (err) {
      console.error('Error al decodificar cuadro remoto:', err);
    } finally {
      this.isRendering = false;
    }
  }

  _drawRemoteCursor(x, y) {
    this.ctx.save();
    this.ctx.beginPath();
    this.ctx.arc(x, y, 6, 0, Math.PI * 2);
    this.ctx.fillStyle = 'rgba(37, 99, 235, 0.85)';
    this.ctx.fill();
    this.ctx.lineWidth = 2;
    this.ctx.strokeStyle = '#ffffff';
    this.ctx.stroke();
    this.ctx.restore();
  }

  _getNormalizedCoords(evt) {
    const rect = this.canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return { x: 0, y: 0 };
    }
    const x = Math.max(0, Math.min(1, (evt.clientX - rect.left) / rect.width));
    const y = Math.max(0, Math.min(1, (evt.clientY - rect.top) / rect.height));
    return { x: Number(x.toFixed(4)), y: Number(y.toFixed(4)) };
  }

  _mapButton(btnCode) {
    if (btnCode === 2) return 'right';
    if (btnCode === 1) return 'middle';
    return 'left';
  }

  _bindInputEvents() {
    this.canvas.addEventListener('contextmenu', (e) => {
      if (this.controlEnabled) {
        e.preventDefault();
      }
    });

    this.canvas.addEventListener('mousemove', (e) => {
      if (!this.controlEnabled || !this.activeAgentId) return;
      const now = performance.now();
      if (now - this.lastMoveSentAt < 35) return;
      this.lastMoveSentAt = now;
      const coords = this._getNormalizedCoords(e);
      this.sendCommand({
        type: 'mouse',
        action: 'move',
        x: coords.x,
        y: coords.y,
      });
    });

    this.canvas.addEventListener('mousedown', (e) => {
      if (!this.controlEnabled || !this.activeAgentId) return;
      this.stage.focus();
      const coords = this._getNormalizedCoords(e);
      this.sendCommand({
        type: 'mouse',
        action: 'down',
        button: this._mapButton(e.button),
        x: coords.x,
        y: coords.y,
      });
    });

    this.canvas.addEventListener('mouseup', (e) => {
      if (!this.controlEnabled || !this.activeAgentId) return;
      const coords = this._getNormalizedCoords(e);
      this.sendCommand({
        type: 'mouse',
        action: 'up',
        button: this._mapButton(e.button),
        x: coords.x,
        y: coords.y,
      });
    });

    this.canvas.addEventListener('dblclick', (e) => {
      if (!this.controlEnabled || !this.activeAgentId) return;
      const coords = this._getNormalizedCoords(e);
      this.sendCommand({
        type: 'mouse',
        action: 'dblclick',
        button: 'left',
        x: coords.x,
        y: coords.y,
      });
    });

    this.canvas.addEventListener(
      'wheel',
      (e) => {
        if (!this.controlEnabled || !this.activeAgentId) return;
        e.preventDefault();
        const coords = this._getNormalizedCoords(e);
        const dy = e.deltaY < 0 ? 2 : -2;
        const dx = e.deltaX < 0 ? -1 : e.deltaX > 0 ? 1 : 0;
        this.sendCommand({
          type: 'mouse',
          action: 'scroll',
          x: coords.x,
          y: coords.y,
          dx,
          dy,
        });
      },
      { passive: false }
    );

    this.stage.addEventListener('keydown', (e) => {
      if (!this.controlEnabled || !this.activeAgentId) return;
      if (['Tab', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', ' ', 'Backspace'].includes(e.key)) {
        e.preventDefault();
      }
      this.sendCommand({
        type: 'key',
        action: 'press',
        key: e.key,
      });
    });
  }
}

