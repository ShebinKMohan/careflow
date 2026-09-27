// CareFlow kiosk microphone capture (M1-14). Research prototype, not for clinical use.
//
// Runs in the AudioWorklet thread: downsamples the microphone from the device rate (usually
// 48 kHz) to 16 kHz, averaging the input samples under each output sample (a box filter against
// aliasing), converts to 16-bit PCM and posts 20 ms frames (320 samples, 640 bytes) to the main
// thread, which sends them straight to the voice service. Nothing is kept after a frame is sent.

class Pcm16Capture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const settings = (options && options.processorOptions) || {};
    this.targetRate = settings.targetRate || 16000;
    this.frameSamples = settings.frameSamples || 320;
    this.ratio = sampleRate / this.targetRate; // `sampleRate` is the AudioWorklet global
    this.pending = new Float32Array(0);
    this.position = 0;
    this.frame = new Int16Array(this.frameSamples);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel || channel.length === 0) return true;
    const data = new Float32Array(this.pending.length + channel.length);
    data.set(this.pending, 0);
    data.set(channel, this.pending.length);

    let position = this.position;
    while (position + this.ratio <= data.length) {
      const start = Math.floor(position);
      const end = Math.max(start + 1, Math.floor(position + this.ratio));
      let sum = 0;
      for (let i = start; i < end; i += 1) sum += data[i];
      const value = Math.max(-1, Math.min(1, sum / (end - start)));
      this.frame[this.filled] = Math.round(value * 32767);
      this.filled += 1;
      if (this.filled === this.frameSamples) {
        const out = this.frame.slice(0).buffer;
        this.port.postMessage(out, [out]);
        this.filled = 0;
      }
      position += this.ratio;
    }
    const consumed = Math.floor(position);
    this.pending = data.slice(consumed);
    this.position = position - consumed;
    return true;
  }
}

registerProcessor("pcm16-capture", Pcm16Capture);
