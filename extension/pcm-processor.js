/**
 * PCMRecorderProcessor — AudioWorkletProcessor
 *
 * Runs in the Web Audio rendering thread at 16 kHz.
 * Downmixes input channels to mono, converts float [-1.0, 1.0] samples
 * to signed 16-bit PCM, and emits 1-second chunks (16,000 samples = 32,000 bytes)
 * via postMessage with transferable ArrayBuffers.
 */
class PCMRecorderProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.channel = options.processorOptions?.channel || "tab";
    // 1 second of audio at 16 kHz mono = 16,000 samples
    this.bufferSize = 16000;
    this.buffer = new Float32Array(this.bufferSize);
    this.bufferIndex = 0;
    this.isStopped = false;

    this.port.onmessage = (event) => {
      if (event.data && event.data.type === "STOP") {
        this.flush();
        this.isStopped = true;
      }
    };
  }

  process(inputs, outputs, parameters) {
    if (this.isStopped) {
      return false;
    }

    const input = inputs[0];
    if (!input || input.length === 0) {
      return true;
    }

    const numChannels = input.length;
    const numSamples = input[0].length;

    for (let i = 0; i < numSamples; i++) {
      // Downmix all channels to mono
      let sum = 0;
      for (let ch = 0; ch < numChannels; ch++) {
        sum += input[ch][i];
      }
      const monoSample = sum / numChannels;

      this.buffer[this.bufferIndex++] = monoSample;

      // When we accumulate 1 second of audio (16,000 samples)
      if (this.bufferIndex >= this.bufferSize) {
        this.flush();
      }
    }

    return true;
  }

  flush() {
    if (this.bufferIndex === 0) return;

    // Convert Float32Array [-1.0, 1.0] to Int16Array [-32768, 32767]
    const pcm16 = new Int16Array(this.bufferIndex);
    for (let i = 0; i < this.bufferIndex; i++) {
      const s = Math.max(-1, Math.min(1, this.buffer[i]));
      pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    // Transfer buffer to offscreen document thread
    this.port.postMessage(
      {
        channel: this.channel,
        pcmBuffer: pcm16.buffer,
        samples: this.bufferIndex,
      },
      [pcm16.buffer]
    );

    this.bufferIndex = 0;
  }
}

registerProcessor("pcm-recorder-processor", PCMRecorderProcessor);
