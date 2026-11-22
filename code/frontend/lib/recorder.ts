"use client";

/** Microphone recorder hook used by the deadline catcher. */

import { useCallback, useEffect, useRef, useState } from "react";

/** Microphone recording for voice notes. Picks a format the phone browser supports
 * (Chrome/Android: webm/opus, Safari/iPhone: mp4) and stops itself after maxSeconds. */
export function useRecorder(maxSeconds = 60) {
  const [state, setState] = useState<"idle" | "recording" | "denied" | "unsupported">("idle");
  const [seconds, setSeconds] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const resolver = useRef<((b: Blob | null) => void) | null>(null);
  const tick = useRef<ReturnType<typeof setInterval> | null>(null);
  const pending = useRef<Blob | null>(null); // a recording that stopped by itself at the time limit

  useEffect(() => {
    if (typeof window !== "undefined" && (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined")) {
      setState("unsupported");
    }
    return () => {
      if (tick.current) clearInterval(tick.current);
      rec.current?.stream.getTracks().forEach((t) => t.stop());
    };
  }, []);

  const start = useCallback(async (): Promise<boolean> => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm"].find((t) => MediaRecorder.isTypeSupported?.(t));
      const r = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      chunks.current = [];
      pending.current = null;
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      r.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = chunks.current.length ? new Blob(chunks.current, { type: r.mimeType || "audio/webm" }) : null;
        if (tick.current) clearInterval(tick.current);
        if (resolver.current) { resolver.current(blob); resolver.current = null; } else pending.current = blob;
      };
      r.start();
      rec.current = r;
      setSeconds(0);
      setState("recording");
      tick.current = setInterval(() => {
        setSeconds((s) => {
          if (s + 1 >= maxSeconds) rec.current?.state === "recording" && rec.current.stop();
          return s + 1;
        });
      }, 1000);
      return true;
    } catch {
      setState("denied");
      return false;
    }
  }, [maxSeconds]);

  const stop = useCallback((): Promise<Blob | null> => {
    if (tick.current) clearInterval(tick.current);
    return new Promise((resolve) => {
      if (!rec.current || rec.current.state !== "recording") {
        setState("idle");
        const b = pending.current;
        pending.current = null;
        return resolve(b);
      }
      resolver.current = (b) => {
        setState("idle");
        resolve(b);
      };
      rec.current.stop();
    });
  }, []);

  return { state, seconds, start, stop };
}

export function extFor(blob: Blob): string {
  return blob.type.includes("mp4") ? "m4a" : blob.type.includes("ogg") ? "ogg" : "webm";
}
