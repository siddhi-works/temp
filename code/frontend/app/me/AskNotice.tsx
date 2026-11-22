"use client";

/** Ask a question about the notice just read, by typing or by voice. The answer comes only from that notice and is spoken aloud. */

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { friendlyError, useT } from "@/lib/i18n";
import { extFor, useRecorder } from "@/lib/recorder";
import { Status, speakWithPhone } from "./Shell";

type Answer = { answer: string; verified: boolean; evidence: string[]; question: string | null; audio_b64: string | null; audio_mime: string };

export default function AskNotice({ token, askToken, lang }: { token: string; askToken: string; lang: string }) {
  const { t } = useT();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<Answer | null>(null);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [audio, setAudio] = useState<string | undefined>();
  const audioRef = useRef<HTMLAudioElement>(null);
  const rec = useRecorder(30);

  useEffect(() => {
    setAudio(undefined);
    if (!res?.audio_b64) return;
    const bytes = Uint8Array.from(atob(res.audio_b64), (c) => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([bytes], { type: res.audio_mime || "audio/mpeg" }));
    setAudio(url);
    return () => URL.revokeObjectURL(url);
  }, [res?.audio_b64, res?.audio_mime]);

  // The answer text is focused, so a screen reader reads it at once. The voice starts only when asked and can be paused:
  // audio that starts by itself talks over TalkBack and cannot be stopped (WCAG 1.4.2).
  const answerRef = useRef<HTMLParagraphElement>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => {
    if (res) answerRef.current?.focus();
  }, [res]);
  const togglePlay = () => {
    const a = audioRef.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => null);
    else a.pause();
  };

  const ask = async (voice?: Blob) => {
    setBusy(true);
    setRes(null);
    setStatus({ text: t("ask_busy") });
    try {
      const f = new FormData();
      f.append("ask_token", askToken);
      f.append("lang", lang);
      if (voice) f.append("audio", voice, `question.${extFor(voice)}`);
      else f.append("question", q);
      setRes(await api<Answer>("/api/me/ask", { token, form: f, timeoutMs: 90000 }));
      setStatus({ text: "" });
    } catch (e) {
      setStatus({ text: friendlyError(e, t), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const toggleVoice = async () => {
    if (rec.state === "recording") {
      const blob = await rec.stop();
      if (!blob || blob.size < 1500) return setStatus({ text: t("ask_too_short"), tone: "error" });
      return ask(blob);
    }
    setStatus({ text: "" });
    await rec.start();
  };

  return (
    <section className="card" aria-labelledby="ask-h">
      <h3 id="ask-h">{t("ask_h")}</h3>
      <p className="muted">{t("ask_note")}</p>
      <form onSubmit={(e) => { e.preventDefault(); if (q.trim().length >= 3) ask(); }} className="stack">
        <label htmlFor="ask-q">{t("ask_label")}</label>
        <input id="ask-q" type="text" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("ask_ph")} autoComplete="off" />
        <button type="submit" className="big-btn main" disabled={busy || rec.state === "recording" || q.trim().length < 3}>{t("ask_btn")}</button>
        {rec.state !== "unsupported" ? (
          <button type="button" className="big-btn" disabled={busy} onClick={toggleVoice}>
            {rec.state === "recording" ? `${t("ask_stop")} (${rec.seconds})` : t("ask_voice")}
          </button>
        ) : null}
      </form>
      <Status text={status.text} tone={status.tone} />
      {res ? (
        <div role="region" aria-label={t("ask_h")} style={{ marginTop: 18 }}>
          {res.question ? <p className="muted">“{res.question}”</p> : null}
          <p lang={lang} ref={answerRef} tabIndex={-1}>{res.answer}</p>
          {res.evidence?.[0] ? <p className="muted">{t("ask_from")}: {res.evidence[0]}</p> : null}
          {audio ? (
            <>
              <audio ref={audioRef} src={audio} preload="auto" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} />
              <button type="button" className="big-btn" onClick={togglePlay}>{playing ? t("pause") : t("ask_play")}</button>
            </>
          ) : (
            <button type="button" className="big-btn" onClick={() => speakWithPhone(res.answer, lang)}>{t("ask_play")}</button>
          )}
        </div>
      ) : null}
    </section>
  );
}
