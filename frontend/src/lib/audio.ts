import { useCallback, useRef, useState } from "react";

/** Encode mono float samples as 16-bit PCM WAV. */
export function encodeWav(samples: Float32Array, rate: number): Blob {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const str = (o: number, s: string) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([buf], { type: "audio/wav" });
}

/** Decode any browser-playable audio and re-encode as mono WAV (models rarely accept webm/opus). */
export async function toWav(blob: Blob, targetRate = 16000): Promise<Blob> {
  if (blob.type === "audio/wav" || blob.type === "audio/x-wav") return blob;
  const ctx = new AudioContext();
  try {
    const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
    const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * targetRate), targetRate);
    const src = offline.createBufferSource();
    src.buffer = decoded;
    src.connect(offline.destination);
    src.start();
    const rendered = await offline.startRendering();
    return encodeWav(rendered.getChannelData(0), targetRate);
  } finally {
    ctx.close();
  }
}

export function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

export function useRecorder() {
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const timer = useRef<number>(0);

  const start = useCallback(async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const chunks: Blob[] = [];
    const r = new MediaRecorder(stream);
    r.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    const done = new Promise<Blob>((resolve) => {
      r.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        resolve(new Blob(chunks, { type: r.mimeType }));
      };
    });
    (r as any)._done = done;
    rec.current = r;
    r.start();
    setRecording(true);
    setSeconds(0);
    const t0 = Date.now();
    timer.current = window.setInterval(() => setSeconds(Math.floor((Date.now() - t0) / 1000)), 250);
  }, []);

  const stop = useCallback(async (): Promise<Blob | null> => {
    const r = rec.current;
    clearInterval(timer.current);
    setRecording(false);
    if (!r) return null;
    r.stop();
    const raw: Blob = await (r as any)._done;
    rec.current = null;
    return toWav(raw);
  }, []);

  return { recording, seconds, start, stop };
}
