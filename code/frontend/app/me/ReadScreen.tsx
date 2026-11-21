"use client";

/** Read-a-notice screen: pick a file, show progress, play the spoken result, and list the dates and what applies to you. */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, speakTime } from "@/lib/api";
import { friendlyError, useT } from "@/lib/i18n";
import { shrinkImage } from "@/lib/image";
import { Status, speakWithPhone } from "./Shell";

const SPEEDS = [0.8, 1, 1.25];

/** Short paragraphs, one or two sentences each: much easier to follow for people with dyslexia or low vision. */
function paragraphs(text: string): string[] {
  // A full stop ends a sentence only after a real word: not after abbreviations like "रु.", "Rs.", "Dr." or "बी.कॉम.".
  const sentences: string[] = [];
  let cur = "";
  for (const tok of (text || "").split(/\s+/).filter(Boolean)) {
    cur = cur ? `${cur} ${tok}` : tok;
    const word = tok.replace(/[.!?।]+$/, "");
    const ends = /[!?।]$/.test(tok) || (/\.$/.test(tok) && word.length >= 3 && !word.includes("."));
    if (ends) { sentences.push(cur); cur = ""; }
  }
  if (cur) sentences.push(cur);
  const out: string[] = [];
  for (let i = 0; i < sentences.length; i += 2) out.push(sentences.slice(i, i + 2).join(" "));
  return out;
}

export default function ReadScreen({ token }: { token: string }) {
  const { t, lang } = useT();
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<any>(null);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [audio, setAudio] = useState<string | undefined>();
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [hasDetails, setHasDetails] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const resultRef = useRef<HTMLHeadingElement>(null);
  const langRef = useRef(lang);
  langRef.current = lang;

  useEffect(() => {
    try {
      setHasDetails(Object.values(JSON.parse(localStorage.getItem("saathi_profile") || "{}")).some((v) => String(v).trim()));
    } catch {}
  }, []);

  const send = useCallback(
    async (file: File) => {
      setBusy(true);
      setRes(null);
      setStatus({ text: t("reading") });
      try {
        const small = await shrinkImage(file, 2000);
        const f = new FormData();
        f.append("file", small, small.name || "notice");
        f.append("lang", langRef.current);
        try {
          const raw = localStorage.getItem("saathi_profile");
          if (raw && Object.values(JSON.parse(raw)).some((v) => String(v).trim())) f.append("profile", raw);
        } catch {}
        setRes(await api("/api/me/read", { token, form: f, timeoutMs: 150000 }));
        setStatus({ text: "" });
      } catch (e) {
        setStatus({ text: friendlyError(e, t), tone: "error" });
      } finally {
        setBusy(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [token],
  );

  // A file shared from WhatsApp / Gallery / Files arrives through the service worker.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("shared") !== "1" || !("caches" in window)) return;
    (async () => {
      try {
        const cache = await caches.open("saathi-share");
        const hit = await cache.match("/shared-file");
        if (!hit) return;
        const blob = await hit.blob();
        await cache.delete("/shared-file");
        window.history.replaceState(null, "", "/me#read");
        await send(new File([blob], blob.type === "application/pdf" ? "shared.pdf" : "shared.jpg", { type: blob.type }));
      } catch {
        setStatus({ text: t("err_read_failed"), tone: "error" });
      }
    })();
  }, [send]);

  // The voice arrives inside the answer and is never stored on the server.
  useEffect(() => {
    setAudio(undefined);
    if (!res?.audio_b64) return;
    const bytes = Uint8Array.from(atob(res.audio_b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: res.audio_mime || "audio/mpeg" }));
    setAudio(url);
    return () => URL.revokeObjectURL(url);
  }, [res?.audio_b64, res?.audio_mime]);

  useEffect(() => {
    if (audioRef.current) audioRef.current.playbackRate = speed;
  }, [speed, audio]);

  const togglePlay = () => {
    const a = audioRef.current;
    if (!a) return;
    a.playbackRate = speed;
    if (a.paused) a.play().catch(() => null);
    else a.pause();
  };

  useEffect(() => {
    if (res) resultRef.current?.focus();
  }, [res]);

  const pick = (
    <>
      {/* One control for a screen reader: a real button that opens the file picker; the file input itself is hidden. */}
      <input id="read-file" ref={fileRef} type="file" accept="image/*,application/pdf" hidden aria-hidden="true" tabIndex={-1} onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); }} />
      <button type="button" className="big-btn main" disabled={busy} onClick={() => fileRef.current?.click()}>
        {busy ? t("reading") : t("choose_file")}
      </button>
    </>
  );

  if (!res) {
    return (
      <div className="stack">
        {pick}
        <Status text={status.text} tone={status.tone} />
        <p className="muted">{t("share_hint")}</p>
        {!hasDetails ? <p className="muted">{t("details_hint")}</p> : null}
      </div>
    );
  }

  return (
    <div>
      <h2 ref={resultRef} tabIndex={-1} style={{ marginTop: 0 }}>{res.title || ""}</h2>

      <section className="card" aria-labelledby="listen-h">
        <h3 id="listen-h">{t("listen")}</h3>
        {audio ? (
          <>
            <audio ref={audioRef} src={audio} preload="auto" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
            <div className="stack">
              <button type="button" className="big-btn main" onClick={togglePlay}>{playing ? t("pause") : t("play")}</button>
              <button type="button" className="big-btn" onClick={() => { if (audioRef.current) audioRef.current.currentTime = Math.max(0, audioRef.current.currentTime - 10); }}>{t("back10")}</button>
            </div>
          </>
        ) : (
          <button type="button" className="big-btn main" onClick={() => speakWithPhone(res.spoken_text, res.lang)}>{t("phone_voice")}</button>
        )}
        {audio ? (
          <div role="group" aria-label={t("speed")} className="seg" style={{ marginTop: 16 }}>
            {SPEEDS.map((s) => (
              <button key={s} type="button" aria-pressed={speed === s} onClick={() => setSpeed(s)}>{s}×</button>
            ))}
          </div>
        ) : null}
      </section>

      {res.personal ? (
        <section className="card" aria-labelledby="foryou-h">
          <h3 id="foryou-h">{t("for_you")}</h3>
          {res.personal.found ? (
            <ul>
              {res.personal.matches.map((m: any, i: number) => (
                <li key={i}>
                  <strong>{m.what}:</strong> {m.detail}
                  <span className="muted" style={{ display: "block" }}>{t("found_in")}: {m.quote}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p>{t("nothing_for_you")}</p>
          )}
          <p className="muted">{t("check_note")}</p>
        </section>
      ) : null}

      <h2>{t("summary")}</h2>
      {paragraphs(res.spoken_text).map((p, i) => (
        <p key={i} lang={res.lang}>{p}</p>
      ))}

      {res.actions?.length ? (
        <>
          <h2>{t("dates_tasks")}</h2>
          <ul>
            {res.actions.map((a: any, i: number) => (
              <li key={i}>
                <strong>{a.label}</strong>
                {a.date_spoken ? `. ${a.date_spoken}${a.due_time ? ", " + speakTime(a.due_time) : ""}` : ""}
                {a.status !== "verified" ? <span className="notice-box" style={{ display: "block", marginTop: 8 }}>{t("not_confirmed")}</span> : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      <div className="stack" style={{ marginTop: 36 }}>
        <button type="button" className="big-btn" onClick={() => { setRes(null); setStatus({ text: "" }); }}>{t("read_another")}</button>
      </div>
    </div>
  );
}
