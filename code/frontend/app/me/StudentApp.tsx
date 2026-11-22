"use client";

/** The student app: sign-in state, the screen router (#read, #catch, #deadlines, #settings) and the deadline screens. */

import { useCallback, useEffect, useRef, useState } from "react";
import Braille from "@/components/Braille";
import { api, ApiError, errText, speakDateLocal, speakTime, WEEKDAYS } from "@/lib/api";
import { shrinkImage } from "@/lib/image";
import { extFor, useRecorder } from "@/lib/recorder";
import { clearDisplay } from "@/lib/display";
import { COLLEGE_MODE } from "@/lib/features";
import { useT } from "@/lib/i18n";
import ReadScreen from "./ReadScreen";
import { IconCalendar, IconCatch, IconRead, IconSettings } from "@/components/Icons";
import SettingsScreen from "./SettingsScreen";
import WhatsAppCard from "./WhatsAppCard";
import { Screen } from "./Shell";
import Welcome from "./Welcome";

type Slot = { weekday: number; start: string; end: string; subject: string; room?: string | null };

/** Status text for a section: shown on screen and announced politely by screen readers. */
function Status({ text, tone }: { text: string; tone?: "error" | "ok" }) {
  return (
    <p role="status" aria-live="polite" className={tone === "error" ? "notice-box" : tone === "ok" ? "ok-box" : "muted"} hidden={!text}>
      {text}
    </p>
  );
}

function readAloud(text: string, lang = "en-IN") {
  // For low-vision users who do not run a screen reader. Screen reader users hear the live region instead.
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = lang;
  u.rate = 0.95;
  window.speechSynthesis.speak(u);
}

type View = "home" | "read" | "catch" | "deadlines" | "settings" | "college";
const VIEWS: View[] = ["read", "catch", "deadlines", "settings", "college"];

export default function StudentApp() {
  const { t } = useT();
  const [token, setToken] = useState<string | null>(null);
  const [inbox, setInbox] = useState<any>(null);
  const [loadErr, setLoadErr] = useState("");
  const [view, setView] = useState<View>("home");

  useEffect(() => {
    const t0 = new URLSearchParams(window.location.search).get("t");
    let saved = "";
    try {
      if (t0) localStorage.setItem("saathi_token", t0);
      saved = t0 || localStorage.getItem("saathi_token") || "";
    } catch {
      saved = t0 || "";
    }
    setToken(saved);
    // Register early so "Share -> Samyukta" works even before the student turns on vibration alerts.
    if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => null);
    return () => clearDisplay();
  }, []);

  // Each task is its own screen, reached through the address (#read, #settings ...), so the phone's Back button works.
  useEffect(() => {
    const sync = () => {
      const h = window.location.hash.replace("#", "") as View;
      const shared = new URLSearchParams(window.location.search).get("shared") === "1";
      setView(shared ? "read" : VIEWS.includes(h) ? h : "home");
    };
    sync();
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);

  const loadInbox = useCallback(async () => {
    if (!token) return;
    try {
      setInbox(await api("/api/me/inbox", { token }));
    } catch (e) {
      setLoadErr(errText(e));
    }
  }, [token]);

  useEffect(() => {
    loadInbox();
  }, [loadInbox]);

  const go = (v: View) => {
    window.location.hash = v === "home" ? "" : v;
  };
  const back = () => go("home");

  if (token === null) return null;
  if (!token || (loadErr && !inbox)) {
    return (
      <Welcome
        expired={!!loadErr}
        onToken={(tk) => {
          try {
            localStorage.setItem("saathi_token", tk);
          } catch {}
          setLoadErr("");
          setToken(tk);
        }}
      />
    );
  }

  const college = COLLEGE_MODE && !!inbox?.student?.college; // college features only exist when college mode is on

  if (view === "read")
    return (
      <Screen title={t("read")} onBack={back}>
        <ReadScreen token={token} />
      </Screen>
    );
  if (view === "catch")
    return (
      <Screen title={t("catch")} onBack={back}>
        <Catcher token={token} />
      </Screen>
    );
  if (view === "deadlines")
    return (
      <Screen title={t("deadlines")} onBack={back}>
        <Deadlines token={token} inbox={inbox} />
      </Screen>
    );
  if (view === "settings")
    return (
      <Screen title={t("settings")} onBack={back}>
        <SettingsScreen extra={<><WhatsAppCard token={token} /><Buzz token={token} /></>} />
      </Screen>
    );
  if (view === "college" && college)
    return (
      <Screen title={t("college")} onBack={back}>
        <RightNow token={token} />
        <Boards token={token} />
        <Notices token={token} inbox={inbox} />
        <Timetable token={token} />
        <Settings token={token} inbox={inbox} onChange={loadInbox} />
      </Screen>
    );

  const items: [View, string, string, boolean][] = [
    ["read", t("read"), t("read_sub"), true],
    ["catch", t("catch"), t("catch_sub"), false],
    ["deadlines", t("deadlines"), t("deadlines_sub"), false],
    ["settings", t("settings"), t("settings_sub"), false],
  ];
  const icons: Record<string, React.ReactNode> = { read: <IconRead />, catch: <IconCatch />, deadlines: <IconCalendar />, settings: <IconSettings /> };
  return (
    <Screen title={t("home_title")}>
      <nav aria-label={t("home_title")} className="stack">
        {items.map(([v, label, sub, main]) => (
          <button key={v} type="button" className={`big-btn has-icon${main ? " main" : ""}`} aria-label={label} aria-describedby={`sub-${v}`} onClick={() => go(v)}>
            <span className="bb-icon">{icons[v]}</span>
            <span className="bb-text">
              {label}
              <span className="sub" id={`sub-${v}`}>{sub}</span>
            </span>
          </button>
        ))}
        {college ? (
          <button type="button" className="big-btn" onClick={() => go("college")}>{t("college")}</button>
        ) : null}
      </nav>
      <p><a href="/about">{t("about_link")}</a></p>
    </Screen>
  );
}

/* ------------------------------------------------------------------ Right now */

function RightNow({ token }: { token: string }) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const ask = async () => {
    setBusy(true);
    setAnswer("");
    try {
      const r = await api("/api/me/now", { token });
      setAnswer(r.text);
    } catch (e) {
      setAnswer(errText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section aria-labelledby="now-h">
      <h2 id="now-h" className="sr-only">Right now</h2>
      <button className="now-slab" onClick={ask} aria-describedby="now-answer" disabled={busy}>
        <span>
          <span className="big">{busy ? "Checking…" : "What’s on right now?"}</span>
          <span className="sub">Your class, the room, and what is due today</span>
        </span>
        <Braille word="now" />
      </button>
      <div id="now-answer" className="answer" role="status" aria-live="polite">
        {answer}
      </div>
      {answer ? (
        <button className="btn" onClick={() => readAloud(answer)}>
          Read it aloud
        </button>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ Verbal deadline catcher */

function Catcher({ token }: { token: string }) {
  const { t, lang } = useT();
  const rec = useRecorder(45);
  const [pending, setPending] = useState<any>(null);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [typed, setTyped] = useState("");
  const [newDate, setNewDate] = useState("");
  const [busy, setBusy] = useState(false);
  const readbackRef = useRef<HTMLHeadingElement>(null);

  // When a read-back arrives, move focus to it so it is read out and Save is the next stop.
  useEffect(() => {
    if (pending?.id) readbackRef.current?.focus();
  }, [pending?.id]);

  const submit = async (form: FormData) => {
    setBusy(true);
    setStatus({ text: t("c_listening") });
    try {
      const r = await api("/api/me/catch", { token, form, timeoutMs: 90000 });
      setPending(r);
      setStatus({ text: "" });
      setNewDate(r.due_date || "");
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const toggle = async () => {
    if (rec.state === "recording") {
      const blob = await rec.stop();
      if (!blob || blob.size < 800) {
        setStatus({ text: t("c_too_short"), tone: "error" });
        return;
      }
      const f = new FormData();
      f.append("audio", blob, `catch.${extFor(blob)}`);
      f.append("lang", lang);
      submit(f);
    } else {
      setPending(null);
      const ok = await rec.start();
      setStatus(ok ? { text: t("c_recording") } : { text: t("c_mic_blocked"), tone: "error" });
    }
  };

  const confirm = async () => {
    setBusy(true);
    try {
      const body = newDate && newDate !== pending.due_date ? { due_date: newDate, lang } : { lang };
      const r = await api(`/api/me/catch/${pending.id}/confirm`, { token, json: body });
      setPending(null);
      setTyped("");
      setStatus({ text: r.message, tone: "ok" });
      window.dispatchEvent(new Event("saathi:deadlines"));
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    await api(`/api/me/catch/${pending.id}/cancel`, { token, method: "POST" }).catch(() => null);
    setPending(null);
    setStatus({ text: t("c_discarded") });
  };

  return (
    <section id="catch" className="feature">
      <p>{t("c_intro")}</p>
      {rec.state !== "unsupported" ? (
        <button className="btn yellow wide rec" aria-pressed={rec.state === "recording"} onClick={toggle} disabled={busy}>
          {rec.state === "recording" ? t("c_stop").replace("{n}", String(rec.seconds)) : t("c_record")}
        </button>
      ) : (
        <p className="hint">{t("c_no_rec")}</p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (typed.trim().length < 3) return setStatus({ text: t("c_type_first"), tone: "error" });
          const f = new FormData();
          f.append("text", typed.trim());
          f.append("lang", lang);
          submit(f);
        }}
      >
        <label htmlFor="catch-text">{t("c_or_type")}</label>
        <input id="catch-text" type="text" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={t("c_placeholder")} autoComplete="off" />
        <div className="row">
          <button className="btn" type="submit" disabled={busy}>
            {t("c_check")}
          </button>
        </div>
      </form>
      <Status {...status} />
      {pending ? (
        <div className="answer" style={{ display: "block" }}>
          <h3 ref={readbackRef} tabIndex={-1} style={{ marginTop: 0 }}>
            {String(pending.readback).replace(/ Say YES.*$/, "")}
          </h3>
          {pending.transcript ? <p className="small muted">{t("c_heard")} &ldquo;{pending.transcript}&rdquo;</p> : null}
          <label htmlFor="catch-date">{pending.due_date ? t("c_date_change") : t("c_date_needed")}</label>
          <input id="catch-date" type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} aria-describedby="catch-date-hint" />
          <p id="catch-date-hint" className="hint">
            {pending.due_date_spoken ? t("c_understood").replace("{d}", speakDateLocal(pending.due_date, lang) + (pending.due_time ? ` ${t("d_at")} ${speakTime(pending.due_time)}` : "")) : t("c_unknown_date")}
          </p>
          <div className="row">
            <button className="btn primary" onClick={confirm} disabled={busy || !newDate}>
              {t("c_save")}
            </button>
            <button className="btn" onClick={discard} disabled={busy}>
              {t("c_discard")}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ Board photos + memory */

function Boards({ token }: { token: string }) {
  const [snaps, setSnaps] = useState<any[]>([]);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const rec = useRecorder(30);

  const load = useCallback(async () => {
    const r = await api("/api/me/snaps", { token });
    setSnaps(r.snaps);
    return r.snaps as any[];
  }, [token]);

  useEffect(() => {
    load().catch(() => null);
  }, [load]);

  const upload = async (file: File) => {
    setStatus({ text: "Saving the photo…" });
    try {
      const small = await shrinkImage(file);
      const f = new FormData();
      f.append("file", small);
      await api("/api/me/snaps", { token, form: f, timeoutMs: 120000 });
      setStatus({ text: "Saved. Reading the board now, this takes a few seconds." });
      for (let i = 0; i < 25; i++) {
        await new Promise((r) => setTimeout(r, 3000));
        const list = await load();
        const latest = list[0];
        if (latest && latest.status !== "processing") {
          setStatus(
            latest.status === "ready"
              ? { text: `Board read${latest.subject ? " for " + latest.subject : ""}. It starts: ${latest.summary?.slice(0, 120) || ""}`, tone: "ok" }
              : { text: "I could not read that board. Try a closer photo with less glare.", tone: "error" },
          );
          return;
        }
      }
      setStatus({ text: "Still reading. It will appear in your list when ready." });
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const ask = async (question: string) => {
    if (question.trim().length < 2) return;
    setAnswer("Searching your boards…");
    try {
      const r = await api("/api/me/memory", { token, query: { q: question.trim() } });
      setAnswer(r.text);
    } catch (e) {
      setAnswer(errText(e));
    }
  };

  const askByVoice = async () => {
    if (rec.state === "recording") {
      const blob = await rec.stop();
      if (!blob) return;
      setAnswer("Searching your boards…");
      const f = new FormData();
      f.append("audio", blob, `q.${extFor(blob)}`);
      try {
        const r = await api("/api/me/memory/voice", { token, form: f, timeoutMs: 90000 });
        setQ(r.question || "");
        setAnswer(r.text);
      } catch (e) {
        setAnswer(errText(e));
      }
    } else {
      const ok = await rec.start();
      if (!ok) setAnswer("The microphone is blocked. Type your question instead.");
    }
  };

  const remove = async (id: string) => {
    await api(`/api/me/snaps/${id}`, { token, method: "DELETE" }).catch(() => null);
    await load();
    setStatus({ text: "Photo deleted." });
  };

  return (
    <section id="boards" className="feature" aria-labelledby="boards-h">
      <h2 id="boards-h">Board photos</h2>
      <p>Photograph the board. It is saved under the class you are in, and you can ask about it later.</p>
      <input
        id="board-file"
        ref={fileRef}
        className="sr-only"
        type="file"
        accept="image/*"
        capture="environment"
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
      />
      <label htmlFor="board-file" className="btn primary wide" style={{ margin: 0 }}>
        Photograph the board
      </label>
      <Status {...status} />

      <form
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          ask(q);
        }}
      >
        <label htmlFor="memory-q">Ask your boards</label>
        <input id="memory-q" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="What was the Carnot efficiency formula?" />
        <div className="row">
          <button className="btn" type="submit">
            Search
          </button>
          {rec.state !== "unsupported" ? (
            <button type="button" className="btn rec" aria-pressed={rec.state === "recording"} onClick={askByVoice}>
              {rec.state === "recording" ? "Stop and search" : "Ask by voice"}
            </button>
          ) : null}
        </div>
      </form>
      <div className="answer" role="status" aria-live="polite">
        {answer}
      </div>

      <h3>Saved boards ({snaps.length})</h3>
      {snaps.length === 0 ? (
        <p className="muted">No board photos yet.</p>
      ) : (
        <ul className="list">
          {snaps.slice(0, 20).map((s) => (
            <li key={s.id}>
              <span className="title">{s.subject || "Board"}</span>,{" "}
              {new Date(s.captured_at).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}
              {s.status === "processing" ? <span className="muted">. Still reading.</span> : null}
              {s.status === "failed" ? <span className="muted">. Could not be read.</span> : null}
              {s.summary ? <p className="small">{s.summary}</p> : null}
              <button className="btn danger" onClick={() => remove(s.id)} aria-label={`Delete ${s.subject || "board"} photo from ${new Date(s.captured_at).toLocaleDateString("en-IN")}`}>
                Delete
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ Deadlines */

function Deadlines({ token, inbox }: { token: string; inbox: any }) {
  const { t, lang } = useT();
  const [mine, setMine] = useState<any[]>([]);
  const load = useCallback(() => {
    api("/api/me/deadlines", { token })
      .then((r) => setMine(r.deadlines))
      .catch(() => null);
  }, [token]);
  useEffect(() => {
    load();
    window.addEventListener("saathi:deadlines", load);
    return () => window.removeEventListener("saathi:deadlines", load);
  }, [load]);

  const college = (inbox?.upcoming || []).map((u: any) => ({
    key: "c" + u.notice_public_id + u.label,
    label: u.label,
    date: u.due_date,
    spoken: speakDateLocal(u.due_date, lang),
    time: u.due_time,
    from: u.notice_title,
    link: `/n/${u.notice_public_id}?t=${encodeURIComponent(token)}`,
    unconfirmed: u.verification === "needs_check",
  }));
  const own = mine.map((d) => ({ key: "p" + d.id, label: d.label, date: d.due_date, spoken: speakDateLocal(d.due_date, lang), time: d.due_time, from: t("d_own") }));
  const all = [...college, ...own].sort((a, b) => a.date.localeCompare(b.date));

  return (
    <section id="deadlines" className="feature">
      {all.length === 0 ? (
        <p className="muted">{t("d_none")}</p>
      ) : (
        <ul className="list">
          {all.map((d: any) => (
            <li key={d.key}>
              <span className="title">{d.label}</span>
              <br />
              {d.spoken}
              {d.time ? ` ${t("d_at")} ${speakTime(d.time)}` : ""}
              <br />
              <span className="small muted">
                {d.link ? <a href={d.link}>{t("d_from")} {d.from}</a> : d.from}
                {d.unconfirmed ? ". " + t("d_check_office") : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ Notices */

function Notices({ token, inbox }: { token: string; inbox: any }) {
  const notices = inbox?.notices || [];
  return (
    <section id="notices" className="feature" aria-labelledby="n-h">
      <h2 id="n-h">Notices</h2>
      {notices.length === 0 ? (
        <p className="muted">No notices from your college yet.</p>
      ) : (
        <ul className="list">
          {notices.map((n: any) => (
            <li key={n.id}>
              <a className="title" href={`/n/${n.public_id}?t=${encodeURIComponent(token)}`}>
                {n.title || "Notice"}
              </a>
              <br />
              <span className="small muted">
                {n.issuer ? n.issuer + ", " : ""}
                {n.published_at ? new Date(n.published_at).toLocaleDateString("en-IN", { day: "numeric", month: "long" }) : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ Timetable */

function Timetable({ token }: { token: string }) {
  const [slots, setSlots] = useState<Slot[]>([]);
  const [batches, setBatches] = useState<string[]>([]);
  const [batch, setBatch] = useState("");
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [draft, setDraft] = useState<Slot>({ weekday: 0, start: "09:00", end: "10:00", subject: "", room: "" });

  const load = useCallback(async () => {
    const r = await api("/api/me/timetable", { token });
    setSlots(r.slots);
    setBatches(r.available_batches);
  }, [token]);
  useEffect(() => {
    load().catch(() => null);
  }, [load]);

  const save = async (next: Slot[], msg: string) => {
    try {
      await api("/api/me/timetable", { token, method: "PUT", json: next });
      await load();
      setStatus({ text: msg, tone: "ok" });
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    }
  };

  const importCollege = async () => {
    try {
      const r = await api("/api/me/timetable/import", { token, json: { batch: batch || null } });
      await load();
      setStatus({ text: `Copied ${r.slots} classes from your college timetable.`, tone: "ok" });
    } catch (e) {
      if (e instanceof ApiError && e.code === "batch_required") setBatches((e.detail as string[]) || []);
      setStatus({ text: errText(e), tone: "error" });
    }
  };

  const byDay = WEEKDAYS.map((name, i) => ({ name, items: slots.filter((s) => s.weekday === i).sort((a, b) => a.start.localeCompare(b.start)) })).filter((d) => d.items.length);

  return (
    <section id="timetable" className="feature" aria-labelledby="tt-h">
      <h2 id="tt-h">My timetable</h2>
      <p>Samyukta uses this to tell you only about notices that clash with your own classes.</p>
      <div className="row">
        {batches.length ? (
          <div style={{ flex: "1 1 14rem" }}>
            <label htmlFor="batch">Your batch</label>
            <select id="batch" value={batch} onChange={(e) => setBatch(e.target.value)}>
              <option value="">Choose your batch</option>
              {batches.map((b) => (
                <option key={b}>{b}</option>
              ))}
            </select>
          </div>
        ) : null}
        <button className="btn" onClick={importCollege} style={{ alignSelf: "flex-end" }}>
          Copy from college timetable
        </button>
      </div>
      <Status {...status} />

      {byDay.length === 0 ? (
        <p className="muted">No classes yet. Copy them from your college timetable or add them below.</p>
      ) : (
        byDay.map((d) => (
          <div key={d.name}>
            <h3>{d.name}</h3>
            <ul className="list">
              {d.items.map((s) => (
                <li key={`${s.weekday}${s.start}${s.subject}`} className="row" style={{ justifyContent: "space-between", margin: 0 }}>
                  <span>
                    <span className="title">{s.subject}</span>, {speakTime(s.start)} to {speakTime(s.end)}
                    {s.room ? `, room ${s.room}` : ""}
                  </span>
                  <button
                    className="btn danger"
                    aria-label={`Remove ${s.subject} on ${d.name} at ${speakTime(s.start)}`}
                    onClick={() => save(slots.filter((x) => x !== s), `Removed ${s.subject}.`)}
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!draft.subject.trim()) return setStatus({ text: "Type the subject name.", tone: "error" });
          save([...slots, { ...draft, subject: draft.subject.trim(), room: draft.room?.trim() || null }], `Added ${draft.subject.trim()} on ${WEEKDAYS[draft.weekday]}.`);
          setDraft({ ...draft, subject: "", room: "" });
        }}
      >
        <fieldset>
          <legend>Add a class</legend>
          <label htmlFor="slot-day">Day</label>
          <select id="slot-day" value={draft.weekday} onChange={(e) => setDraft({ ...draft, weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((d, i) => (
              <option key={d} value={i}>
                {d}
              </option>
            ))}
          </select>
          <label htmlFor="slot-start">Starts</label>
          <input id="slot-start" type="time" value={draft.start} onChange={(e) => setDraft({ ...draft, start: e.target.value })} required />
          <label htmlFor="slot-end">Ends</label>
          <input id="slot-end" type="time" value={draft.end} onChange={(e) => setDraft({ ...draft, end: e.target.value })} required />
          <label htmlFor="slot-subject">Subject</label>
          <input id="slot-subject" type="text" value={draft.subject} onChange={(e) => setDraft({ ...draft, subject: e.target.value })} />
          <label htmlFor="slot-room">Room (optional)</label>
          <input id="slot-room" type="text" value={draft.room || ""} onChange={(e) => setDraft({ ...draft, room: e.target.value })} />
          <div className="row">
            <button className="btn primary" type="submit">
              Add class
            </button>
          </div>
        </fieldset>
      </form>
    </section>
  );
}

/* ------------------------------------------------------------------ Buzz codes */

const PRESETS: { name: string; pattern: number[] }[] = [
  { name: "One long buzz", pattern: [600] },
  { name: "One short buzz", pattern: [200] },
  { name: "Two medium buzzes", pattern: [250, 150, 250] },
  { name: "Three long buzzes", pattern: [450, 150, 450, 150, 450] },
  { name: "Four quick buzzes", pattern: [90, 70, 90, 70, 90, 70, 90] },
  { name: "Short, then long", pattern: [120, 120, 500] },
  { name: "Heartbeat", pattern: [100, 80, 100, 500, 100, 80, 100] },
];

function urlB64ToUint8Array(base64: string) {
  const pad = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

function Buzz({ token }: { token: string }) {
  const [codes, setCodes] = useState<any[]>([]);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [support, setSupport] = useState({ push: false, vibrate: false });

  useEffect(() => {
    api("/api/me/buzz-codes", { token })
      .then((r) => setCodes(r.codes))
      .catch(() => null);
    const push = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
    setSupport({ push, vibrate: "vibrate" in navigator });
    if (push) {
      navigator.serviceWorker.getRegistration("/").then(async (reg) => setSubscribed(!!(reg && (await reg.pushManager.getSubscription()))));
    }
  }, [token]);

  const enable = async () => {
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") return setStatus({ text: "Notifications are blocked. Allow them for this site in your browser settings.", tone: "error" });
      const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      const { public_key } = await api("/api/public/push-key");
      const sub = (await reg.pushManager.getSubscription()) || (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlB64ToUint8Array(public_key) }));
      const json = sub.toJSON();
      await api("/api/me/push/subscribe", { token, json: { endpoint: json.endpoint, keys: json.keys } });
      setSubscribed(true);
      setStatus({ text: "Vibration alerts are on for this phone.", tone: "ok" });
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    }
  };

  const feel = (pattern: number[]) => {
    if (support.vibrate) navigator.vibrate(pattern);
    else setStatus({ text: "This phone cannot vibrate from a web page. Vibration works on Android phones with Chrome." });
  };

  const change = async (type: string, value: string) => {
    const pattern = value === "default" ? null : PRESETS[Number(value)].pattern;
    try {
      const r = await api(`/api/me/buzz-codes/${type}`, { token, method: "PUT", json: { pattern } });
      setCodes(r.codes);
      if (pattern) feel(pattern);
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    }
  };

  const test = async (type: string) => {
    try {
      const r = await api("/api/me/push/test", { token, method: "POST", query: { kind: type } });
      setStatus({ text: r.sent_to_devices ? "Test alert sent. Lock the phone to feel it." : "No phone is set up yet. Turn on vibration alerts first.", tone: r.sent_to_devices ? "ok" : "error" });
    } catch (e) {
      setStatus({ text: errText(e), tone: "error" });
    }
  };

  return (
    <section id="buzz" className="feature" aria-labelledby="buzz-h">
      <h2 id="buzz-h">Vibration alerts</h2>
      <p>Each kind of message has its own vibration, so you know what arrived without taking the phone out. This works on Android phones with Chrome.</p>
      {support.push ? (
        subscribed ? (
          <p className="ok-box">Vibration alerts are on for this phone.</p>
        ) : (
          <button className="btn primary wide" onClick={enable}>
            Turn on vibration alerts
          </button>
        )
      ) : (
        <p className="hint">This browser cannot receive alerts. You will still get everything on WhatsApp.</p>
      )}
      <Status {...status} />
      <ul className="list">
        {codes.map((c) => {
          const idx = PRESETS.findIndex((p) => JSON.stringify(p.pattern) === JSON.stringify(c.pattern));
          return (
            <li key={c.type}>
              <span className="title">{c.name}</span>
              <label htmlFor={`buzz-${c.type}`} className="small">
                Vibration for {c.name.toLowerCase()}
              </label>
              <select id={`buzz-${c.type}`} value={c.customised ? String(idx) : "default"} onChange={(e) => change(c.type, e.target.value)}>
                <option value="default">Default: {c.describe}</option>
                {PRESETS.map((p, i) => (
                  <option key={p.name} value={i}>
                    {p.name}
                  </option>
                ))}
              </select>
              <div className="row">
                <button className="btn" onClick={() => feel(c.pattern)} aria-label={`Feel the vibration for ${c.name.toLowerCase()}`}>
                  Feel it
                </button>
                {subscribed ? (
                  <button className="btn" onClick={() => test(c.type)} aria-label={`Send a test alert for ${c.name.toLowerCase()}`}>
                    Send test alert
                  </button>
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ Settings */

function Settings({ token, inbox, onChange }: { token: string; inbox: any; onChange: () => void }) {
  const [status, setStatus] = useState("");
  const student = inbox?.student || {};
  const update = async (body: object, msg: string) => {
    try {
      await api("/api/me/preferences", { token, method: "PATCH", json: body });
      setStatus(msg);
      onChange();
    } catch (e) {
      setStatus(errText(e));
    }
  };
  return (
    <section id="settings" className="feature" aria-labelledby="set-h">
      <h2 id="set-h">Settings</h2>
      <label htmlFor="lang">Language for voice notes and messages</label>
      <select id="lang" value={student.preferred_lang || "en"} onChange={(e) => update({ preferred_lang: e.target.value }, "Language saved.")}>
        <option value="en">English</option>
        <option value="mr" lang="mr">
          मराठी (Marathi)
        </option>
        <option value="hi" lang="hi">
          हिन्दी (Hindi)
        </option>
      </select>
      <label className="check" htmlFor="rem" style={{ marginTop: 18 }}>
        <input id="rem" type="checkbox" checked={student.reminders_enabled !== false} onChange={(e) => update({ reminders_enabled: e.target.checked }, e.target.checked ? "Reminders on." : "Reminders off.")} />
        Send me reminders before deadlines
      </label>
      <Status text={status} />
    </section>
  );
}
