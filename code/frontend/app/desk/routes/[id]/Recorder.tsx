"use client";

/** Records the voice instructions for one step of a campus walkthrough route. */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Header from "@/components/Header";
import { api, errText, rel } from "@/lib/api";
import { extFor, useRecorder } from "@/lib/recorder";

function token(): string {
  try {
    return localStorage.getItem("saathi.staff") || "";
  } catch {
    return "";
  }
}

export default function Recorder({ id }: { id: string }) {
  const [staff, setStaff] = useState<string | null>(null);
  const [route, setRoute] = useState<any>(null);
  const [text, setText] = useState("");
  const [clip, setClip] = useState<Blob | null>(null);
  const [clipUrl, setClipUrl] = useState("");
  const [msg, setMsg] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [busy, setBusy] = useState(false);
  const rec = useRecorder(40);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => setStaff(token()), []);
  const load = useCallback(async () => {
    if (!staff) return;
    const r = await api("/api/routes", { staff });
    const found = r.routes.find((x: any) => x.id === id);
    if (!found) throw new Error("Walkthrough not found.");
    setRoute(found);
  }, [staff, id]);
  useEffect(() => {
    load().catch((e) => setMsg({ text: errText(e), tone: "error" }));
  }, [load]);

  useEffect(() => () => {
    if (clipUrl) URL.revokeObjectURL(clipUrl);
  }, [clipUrl]);

  if (staff === null) return null;
  if (!staff)
    return (
      <>
        <Header wide />
        <main id="main" className="wrap">
          <h1>Sign in first</h1>
          <p>
            <Link href="/desk">Go to the college desk to sign in.</Link>
          </p>
        </main>
      </>
    );

  const toggle = async () => {
    if (rec.state === "recording") {
      const b = await rec.stop();
      if (b) {
        setClip(b);
        setClipUrl(URL.createObjectURL(b));
        setMsg({ text: "Clip recorded. Now type what you said, then add the step." });
        textRef.current?.focus();
      }
    } else {
      setClip(null);
      setClipUrl("");
      const ok = await rec.start();
      if (!ok) setMsg({ text: "The microphone is blocked. Allow it in the browser settings.", tone: "error" });
    }
  };

  const addStep = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return setMsg({ text: "Type the instruction for this step. Students who cannot hear the clip will read it.", tone: "error" });
    setBusy(true);
    const sent = text;
    const f = new FormData();
    f.append("text", sent.trim());
    if (clip) f.append("audio", clip, `step.${extFor(clip)}`);
    try {
      await api(`/api/routes/${id}/steps`, { staff, form: f, timeoutMs: 60000 });
      setText((cur) => (cur === sent ? "" : cur)); // keep anything typed while saving
      setClip(null);
      setClipUrl("");
      await load();
      setMsg({ text: "Step added. Walk to the next point and record the next step.", tone: "ok" });
    } catch (err) {
      setMsg({ text: errText(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const act = async (path: string, done: string) => {
    try {
      await api(`/api/routes/${id}/${path}`, { staff, method: "POST" });
      await load();
      setMsg({ text: done, tone: "ok" });
    } catch (err) {
      setMsg({ text: errText(err), tone: "error" });
    }
  };

  const removeStep = async (stepId: string) => {
    try {
      await api(`/api/routes/${id}/steps/${stepId}`, { staff, method: "DELETE" });
      await load();
      setMsg({ text: "Step removed." });
    } catch (err) {
      setMsg({ text: errText(err), tone: "error" });
    }
  };

  const live = route?.status === "published";
  return (
    <>
      <Header wide />
      <main id="main" className="wrap">
        <p>
          <Link href="/desk">Back to the desk</Link>
        </p>
        <h1>{route?.title || "Walkthrough"}</h1>
        {route ? (
          <p className="lede">
            From {route.from} to {route.to}. <span className={live ? "tag ok" : "tag"}>{live ? "Live" : "Draft"}</span>
          </p>
        ) : null}

        <p role="status" aria-live="polite" className={msg.tone === "error" ? "notice-box" : msg.tone === "ok" ? "ok-box" : "muted"} hidden={!msg.text}>
          {msg.text}
        </p>

        <h2>Steps</h2>
        {route?.steps.length ? (
          <ol className="list">
            {route.steps.map((s: any) => (
              <li key={s.id}>
                <span className="title">{s.text}</span>
                {s.audio_url ? (
                  <div>
                    <audio controls preload="none" src={rel(s.audio_url)} aria-label={`Voice clip for step ${s.n}`} />
                  </div>
                ) : null}
                {!live ? (
                  <button className="btn danger" onClick={() => removeStep(s.id)} aria-label={`Remove step ${s.n}`}>
                    Remove
                  </button>
                ) : null}
              </li>
            ))}
          </ol>
        ) : (
          <p className="muted">No steps yet. Stand at the start, then record the first step.</p>
        )}

        {!live ? (
          <form onSubmit={addStep} className="feature">
            <h2>Add the next step</h2>
            <p className="hint">Use landmarks a cane or feet can find: steps, ramps, a wall, a change from tiles to grass. Count steps where it helps.</p>
            {rec.state !== "unsupported" ? (
              <button type="button" className="btn yellow wide rec" aria-pressed={rec.state === "recording"} onClick={toggle}>
                {rec.state === "recording" ? `Stop recording (${rec.seconds} s)` : clip ? "Record again" : "Record this step"}
              </button>
            ) : null}
            {clipUrl ? <audio controls src={clipUrl} aria-label="Your recorded clip" style={{ marginTop: 10 }} /> : null}
            <label htmlFor="step-text">What the clip says</label>
            <textarea id="step-text" ref={textRef} value={text} onChange={(e) => setText(e.target.value)} placeholder="Walk straight 20 steps with the wall on your left. Stop at the three steps going down." />
            <div className="row">
              <button className="btn primary" type="submit" disabled={busy}>
                Add step
              </button>
            </div>
          </form>
        ) : null}

        <div className="feature">
          <h2>Publish</h2>
          {live ? (
            <>
              <p>
                Students can play this now. Print the QR code and fix it at {route?.from}.
              </p>
              <p>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={`/api/public/routes/${id}/qr.png`} alt={`QR code that opens the walkthrough ${route?.title}`} width={220} height={220} />
              </p>
              <div className="row">
                <Link className="btn" href={`/r/${id}`}>
                  Play it as a student
                </Link>
                <button className="btn" onClick={() => act("unpublish", "Taken offline. You can edit it again.")}>
                  Take offline to edit
                </button>
              </div>
            </>
          ) : (
            <>
              <p>Someone other than the person who recorded it must walk the route with the clips and then publish it.</p>
              <button className="btn primary" onClick={() => act("publish", "Published. Students can play it now.")} disabled={!route?.steps.length}>
                I walked it, publish
              </button>
            </>
          )}
        </div>
      </main>
    </>
  );
}
