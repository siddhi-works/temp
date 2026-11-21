"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, errText, speakTime } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { shrinkImage } from "@/lib/image";
import { Status, speakWithPhone } from "./Shell";

const SPEEDS = [0.8, 1, 1.25];

/** Short paragraphs, one or two sentences each: much easier to follow for people with dyslexia or low vision. */
function paragraphs(text: string): string[] {
  const sentences = (text || "").split(/(?<=[.!?।])\s+/).filter(Boolean);
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
        setStatus({ text: errText(e), tone: "error" });
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
        setStatus({ text: errText(null), tone: "error" });
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

  useEffect(() => {
    if (res) resultRef.current?.focus();
  }, [res]);

  const pick = (
    <>
      {/* The input comes first so the button after it can show the focus ring (see me.css: input:focus-visible + label). */}
      <input id="read-file" ref={fileRef} type="file" accept="image/*,application/pdf" className="sr-only" disabled={busy} onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); }} />
      <label className="big-btn main" htmlFor="read-file" style={{ cursor: "pointer", opacity: busy ? 0.6 : 1 }}>
        {busy ? t("reading") : t("choose_file")}
      </label>
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
          <audio ref={audioRef} controls preload="auto" src={audio} aria-label={t("listen")} />
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
