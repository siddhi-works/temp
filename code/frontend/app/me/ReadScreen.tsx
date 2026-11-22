"use client";

/** Read-a-notice screen: pick a file, show progress, play the spoken result, and list the dates and what applies to you. */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, speakTime } from "@/lib/api";
import { friendlyError, useT } from "@/lib/i18n";
import { shrinkImage } from "@/lib/image";
import { IconAlert, IconCheck, IconPlay, IconShield } from "@/components/Icons";
import { downloadIcs } from "@/lib/ics";
import { sampleResult } from "@/lib/sample";
import AskNotice from "./AskNotice";
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
      {busy ? <span className="loader" aria-hidden="true"><i /><i /><i /></span> : null}
      <button type="button" className="big-btn has-icon" disabled={busy} onClick={() => setRes(sampleResult(langRef.current as "en" | "hi" | "mr"))}>
        <span className="bb-icon"><IconPlay /></span>
        <span className="bb-text">{t("sample_btn")}<span className="sub">{t("sample_hint")}</span></span>
      </button>
    </>
  );

  const calendarItems = (res?.actions || [])
    .filter((a: any) => a.status === "verified" && a.due_date)
    .map((a: any) => ({ label: a.label, due_date: a.due_date, due_time: a.due_time, venue: a.venue }));

  const nOk = (res?.actions || []).filter((a: any) => a.status === "verified").length;
  const nBad = (res?.actions || []).filter((a: any) => a.status !== "verified").length;
  const nSafe = (res?.safety || []).filter((f: any) => f.level === "caution").length;

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
      {res.sample ? <p className="notice-box" role="note">{t("sample_badge")}</p> : null}
      <ul className="verdict" aria-label={t("v_head")}>
        {nOk ? <li className="v ok"><IconCheck size={24} />{t("v_checked").replace("{n}", String(nOk))}</li> : null}
        {nBad ? <li className="v warn"><IconAlert size={24} />{t("v_unconfirmed").replace("{n}", String(nBad))}</li> : null}
        {nSafe ? <li className="v warn"><IconShield size={24} />{t("v_safety").replace("{n}", String(nSafe))}</li> : null}
      </ul>

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

      {res.safety?.length ? (
        <section className={`card ${res.safety.some((f: any) => f.level === "caution") ? "safety" : "info-card"}`} aria-labelledby="safety-h">
          <div className="p-head"><IconShield /><h3 id="safety-h">{t("safety_h")}</h3></div>
          <ul>
            {res.safety.map((f: any, i: number) => (
              <li key={i}>
                {f.text}
                <span className="muted" style={{ display: "block" }}>{t("found_in")}: {f.evidence}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

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
          <p className="score">{t("dates_checked").replace("{n}", String(res.actions.filter((a: any) => a.status === "verified").length)).replace("{total}", String(res.actions.length))}</p>
          <ul className="proofs">
            {res.actions.map((a: any, i: number) => {
              const ok = a.status === "verified";
              return (
                <li key={i} className={`proof ${ok ? "ok" : "warn"}`}>
                  <div className="p-head">
                    <span className="p-icon">{ok ? <IconCheck /> : <IconAlert />}</span>
                    <div>
                      <strong>{a.label}</strong>
                      {a.date_spoken ? `. ${a.date_spoken}${a.due_time ? ", " + speakTime(a.due_time) : ""}` : ""}
                      <span className="p-status">{ok ? t("proof_ok") : t("not_confirmed")}</span>
                    </div>
                  </div>
                  {ok && a.evidence ? <p className="quote" lang={res.lang}><span className="muted">{t("proof_from")}: </span>{a.evidence}</p> : null}
                </li>
              );
            })}
          </ul>
          {calendarItems.length ? (
            <div className="stack" style={{ marginTop: 18 }}>
              <button type="button" className="big-btn" onClick={() => { downloadIcs(calendarItems); setStatus({ text: t("cal_done"), tone: "ok" }); }}>{t("cal_btn")}</button>
              <p className="muted">{t("cal_note")}</p>
              <Status text={status.text} tone={status.tone} />
            </div>
          ) : null}
        </>
      ) : null}

      {res.ask_token ? <AskNotice token={token} askToken={res.ask_token} lang={res.lang} /> : null}

      <div className="stack" style={{ marginTop: 36 }}>
        <button type="button" className="big-btn" onClick={() => { setRes(null); setStatus({ text: "" }); }}>{t("read_another")}</button>
      </div>
    </div>
  );
}
