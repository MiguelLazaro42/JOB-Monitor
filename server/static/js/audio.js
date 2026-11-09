class PushToTalkController {
  constructor(buttonEl, sendChunkCallback, onStartCallback) {
    this.btn = buttonEl;
    this.sendChunk = sendChunkCallback;
    this.onStart = onStartCallback;

    this.isRecording = false;
    this.audioCtx = null;
    this.mediaStream = null;
    this.sourceNode = null;
    this.processorNode = null;
    this.pcmChunks = [];
    this.sampleRate = 16000;
    this.flushTimer = null;

    if (this.btn) {
      this._bindEvents();
    }
  }

  _bindEvents() {
    const startHandler = (e) => {
      e.preventDefault();
      this.startRecording();
    };
    const stopHandler = (e) => {
      e.preventDefault();
      this.stopRecording();
    };

    this.btn.addEventListener('mousedown', startHandler);
    window.addEventListener('mouseup', () => {
      if (this.isRecording) this.stopRecording();
    });
    this.btn.addEventListener('mouseleave', () => {
      if (this.isRecording) this.stopRecording();
    });
    this.btn.addEventListener('touchstart', startHandler, { passive: false });
    this.btn.addEventListener('touchend', stopHandler, { passive: false });
    this.btn.addEventListener('touchcancel', stopHandler, { passive: false });
  }

  async startRecording() {
    if (this.isRecording) return;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      alert('Tu navegador no permite acceso al micrófono en este contexto.');
      return;
    }

    try {
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          channelCount: 1,
        },
        video: false,
      });

      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.audioCtx = new AudioCtx();
      const inputRate = this.audioCtx.sampleRate;

      this.sourceNode = this.audioCtx.createMediaStreamSource(this.mediaStream);
      this.processorNode = this.audioCtx.createScriptProcessor(4096, 1, 1);
      this.pcmChunks = [];
      this.isRecording = true;

      this._updateButtonUI(true);
      if (typeof this.onStart === 'function') {
        this.onStart();
      }

      this.processorNode.onaudioprocess = (evt) => {
        if (!this.isRecording) return;
        const inputData = evt.inputBuffer.getChannelData(0);
        const downsampled = this._downsampleBuffer(inputData, inputRate, this.sampleRate);
        if (downsampled && downsampled.length > 0) {
          this.pcmChunks.push(downsampled);
        }
      };

      this.sourceNode.connect(this.processorNode);
      this.processorNode.connect(this.audioCtx.destination);

      this.flushTimer = setInterval(() => {
        this._flushCurrentBuffer();
      }, 650);
    } catch (err) {
      console.error('No se pudo acceder al micrófono para Push-to-Talk:', err);
      this.isRecording = false;
      this._updateButtonUI(false);
    }
  }

  stopRecording() {
    if (!this.isRecording) return;
    this.isRecording = false;

    if (this.flushTimer) {
      clearInterval(this.flushTimer);
      this.flushTimer = null;
    }

    this._flushCurrentBuffer();

    try {
      if (this.processorNode) {
        this.processorNode.disconnect();
        this.processorNode.onaudioprocess = null;
      }
      if (this.sourceNode) {
        this.sourceNode.disconnect();
      }
      if (this.mediaStream) {
        this.mediaStream.getTracks().forEach((t) => t.stop());
      }
      if (this.audioCtx && this.audioCtx.state !== 'closed') {
        this.audioCtx.close();
      }
    } catch (e) {
      // ignore cleanup errors
    }

    this.processorNode = null;
    this.sourceNode = null;
    this.mediaStream = null;
    this.audioCtx = null;
    this._updateButtonUI(false);
  }

  _updateButtonUI(active) {
    if (!this.btn) return;
    this.btn.classList.toggle('btn-ptt-active', active);
    const labelEl = this.btn.querySelector('.js-ptt-label');
    if (labelEl) {
      labelEl.textContent = active ? 'Transmitiendo Voz...' : 'Mantener para Hablar';
    }
  }

  _downsampleBuffer(buffer, inputSampleRate, outSampleRate) {
    if (outSampleRate >= inputSampleRate) {
      return new Float32Array(buffer);
    }
    const ratio = inputSampleRate / outSampleRate;
    const newLength = Math.round(buffer.length / ratio);
    const result = new Float32Array(newLength);
    let offsetResult = 0;
    let offsetBuffer = 0;
    while (offsetResult < result.length) {
      const nextOffsetBuffer = Math.round((offsetResult + 1) * ratio);
      let accum = 0;
      let count = 0;
      for (let i = offsetBuffer; i < nextOffsetBuffer && i < buffer.length; i++) {
        accum += buffer[i];
        count++;
      }
      result[offsetResult] = count > 0 ? accum / count : 0;
      offsetResult++;
      offsetBuffer = nextOffsetBuffer;
    }
    return result;
  }

  _flushCurrentBuffer() {
    if (!this.pcmChunks || this.pcmChunks.length === 0) return;

    let totalLen = 0;
    for (const chunk of this.pcmChunks) {
      totalLen += chunk.length;
    }
    if (totalLen < 800) return;

    const merged = new Float32Array(totalLen);
    let offset = 0;
    for (const chunk of this.pcmChunks) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    this.pcmChunks = [];

    const wavBuffer = this._encodeWAV(merged, this.sampleRate);
    const b64 = this._arrayBufferToBase64(wavBuffer);
    if (b64 && typeof this.sendChunk === 'function') {
      this.sendChunk(b64);
    }
  }

  _encodeWAV(samples, sampleRate) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);

    const writeString = (offset, str) => {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i));
      }
    };

    writeString(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true); // Subchunk1Size (PCM)
    view.setUint16(20, 1, true);  // AudioFormat (1 = PCM)
    view.setUint16(22, 1, true);  // NumChannels (1 = Mono)
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true); // ByteRate
    view.setUint16(32, 2, true);  // BlockAlign
    view.setUint16(34, 16, true); // BitsPerSample
    writeString(36, 'data');
    view.setUint32(40, samples.length * 2, true);

    let offset = 44;
    for (let i = 0; i < samples.length; i++, offset += 2) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }

    return buffer;
  }

  _arrayBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = '';
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunkSize));
    }
    return window.btoa(binary);
  }
}
