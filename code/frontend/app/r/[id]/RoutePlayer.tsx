"use client";

import { useEffect, useRef, useState } from "react";
import Header from "@/components/Header";
import { api, errText, rel } from "@/lib/api";

type Step = { id: string; n: number; text: string; audio_url: string | null };
type RouteData = { id: string; title: string; from: string; to: string; steps: Step[]; verified_by?: string };

export default function RoutePlayer({ id }: { id: string }) {
  const [route, setRoute] = useState<RouteData | null>(null);
  const [err, setErr] = useState("");
  const [i, setI] = useState(-1); // -1 = not started
  const [voice, setVoice] = useState(true);
  const audio = useRef<HTMLAudioElement>(null);
  const stepRef = useRef<HTMLParagraphElement>(null);

  useEffect(() => {
    api(`/api/public/routes/${encodeURIComponent(id)}`)
      .then(setRoute)
      .catch((e) => setErr(errText(e)));
  }, [id]);

  const play = (n: number) => {
    const st = route?.steps[n];
    const a = audio.current;
    if (!a) return;
    a.pause();
    if (voice && st?.audio_url) {
      a.src = rel(st.audio_url)!;
      a.play().catch(() => null);
    }
  };

  const go = (n: number) => {
    if (!route) return;
    const next = Math.max(0, Math.min(route.steps.length - 1, n));
    setI(next);
    play(next);
  };

  // Move focus to the instruction after every step change, so a screen reader reads it straight away.
  useEffect(() => {
    if (i >= 0) stepRef.current?.focus();
  }, [i]);

  if (err) {
    return (
      <>
        <Header />
        <main id="main" className="wrap">
          <h1>Walkthrough not available</h1>
          <p className="notice-box">{err}</p>
        </main>
      </>
    );
  }
  if (!route) {
    return (
      <>
        <Header />
        <main id="main" className="wrap" aria-busy="true">
          <p role="status">Loading the walkthrough…</p>
        </main>
      </>
    );
  }

  const total = route.steps.length;
  const step = i >= 0 ? route.steps[i] : null;
  const last = i === total - 1;

  return (
    <>
      <Header />
      <main id="main" className="wrap">
        <h1>{route.title}</h1>
        <p className="lede">
          From {route.from} to {route.to}. {total} steps.
        </p>
        {route.verified_by ? <p className="small muted">Route checked by {route.verified_by}.</p> : null}
        <audio ref={audio} preload="none" />

        {step === null ? (
          <>
            <label className="check" htmlFor="voice">
              <input id="voice" type="checkbox" checked={voice} onChange={(e) => setVoice(e.target.checked)} />
              Play the volunteer&rsquo;s voice for each step
            </label>
            <div className="row">
              <button className="btn yellow wide" style={{ minHeight: 90, fontSize: "1.5rem" }} onClick={() => go(0)}>
                Start walking
              </button>
            </div>
            <p className="hint">Hold the phone in your hand. After each step, press Next. You can go back or repeat at any time.</p>
          </>
        ) : (
          <>
            <div className="path" aria-hidden="true">
              {route.steps.map((s, k) => (
                <span key={s.id} className={k < i ? "done" : k === i ? "here" : ""} />
              ))}
            </div>
            <p className="step-count">
              Step {i + 1} of {total}
            </p>
            <p className="step-text" ref={stepRef} tabIndex={-1} aria-live="polite">
              {step.text}
            </p>
            <div className="player-controls">
              <button className="btn" onClick={() => go(i - 1)} disabled={i === 0}>
                Back
              </button>
              <button className="btn" onClick={() => go(i)}>
                Repeat
              </button>
              <button className="btn primary" onClick={() => (last ? setI(-2) : go(i + 1))}>
                {last ? "Finish" : "Next"}
              </button>
            </div>
          </>
        )}
        {i === -2 ? (
          <div className="ok-box" role="status">
            You have reached {route.to}.{" "}
            <button className="btn" onClick={() => go(0)}>
              Play again from the start
            </button>
          </div>
        ) : null}
      </main>
    </>
  );
}
