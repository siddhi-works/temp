"use client";

/** Staff desk: upload notices, review and approve the dates, and manage volunteers. Used by the college side. */

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Header from "@/components/Header";
import { api, errText, rel, speakTime } from "@/lib/api";
import { shrinkImage } from "@/lib/image";

const KEY = "saathi.staff";
function loadToken(): string {
  try {
    return localStorage.getItem(KEY) || "";
  } catch {
    return "";
  }
}
function saveToken(t: string) {
  try {
    t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY);
  } catch {
    /* private mode: stays signed in for this tab only */
  }
}

export default function Desk() {
  const [token, setToken] = useState<string | null>(null);
  const [me, setMe] = useState<any>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<"notices" | "routes" | "students" | "team">("notices");

  useEffect(() => setToken(loadToken()), []);
  useEffect(() => {
    if (!token) return;
    api("/api/me", { staff: token })
      .then((m) => {
        setMe(m);
        setErr("");
      })
      .catch((e) => {
        setErr(errText(e));
        setMe(null);
      });
  }, [token]);

  if (token === null) return null;
  if (!token || !me) return <SignIn error={err} onToken={(t) => (saveToken(t), setToken(t))} />;

  const tabs: [typeof tab, string][] = [
    ["notices", "Notices"],
    ["routes", "Walkthroughs"],
    ["students", "Students"],
    ...(me.role === "admin" ? ([["team", "Team"]] as [typeof tab, string][]) : []),
  ];
  return (
    <>
      <Header wide who={`${me.name}, ${me.college.name}`} />
      <main id="main" className="wrap-wide">
        <h1>College desk</h1>
        <div className="tabs" role="tablist" aria-label="Desk sections">
          {tabs.map(([k, label]) => (
            <button key={k} role="tab" id={`tab-${k}`} aria-controls={`panel-${k}`} aria-selected={tab === k} tabIndex={tab === k ? 0 : -1} onClick={() => setTab(k)}
              onKeyDown={(e) => {
                const idx = tabs.findIndex(([x]) => x === k);
                const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
                if (!d) return;
                const next = tabs[(idx + d + tabs.length) % tabs.length][0];
                setTab(next);
                document.getElementById(`tab-${next}`)?.focus();
              }}>
              {label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} style={{ paddingTop: 20 }}>
          {tab === "notices" && <Notices token={token} canPublish={me.role !== "verifier"} />}
          {tab === "routes" && <Routes token={token} me={me} />}
          {tab === "students" && <Students token={token} />}
          {tab === "team" && <Team token={token} />}
        </div>
        <p className="foot">
          <button className="btn" onClick={() => (saveToken(""), setToken(""), setMe(null))}>
            Sign out
          </button>
        </p>
      </main>
    </>
  );
}

/* ------------------------------------------------------------------ sign in / first setup */

function SignIn({ error, onToken }: { error: string; onToken: (t: string) => void }) {
  const [t, setT] = useState("");
  const [setup, setSetup] = useState({ college_code: "", college_name: "", admin_name: "", admin_office: "Exam Section" });
  const [setupErr, setSetupErr] = useState("");
  const [newToken, setNewToken] = useState("");
  return (
    <>
      <Header wide />
      <main id="main" className="wrap">
        <h1>College desk</h1>
        {error ? <p className="notice-box">{error}</p> : null}
        <form onSubmit={(e) => (e.preventDefault(), t.trim() && onToken(t.trim()))}>
          <label htmlFor="tok">Your sign-in token</label>
          <input id="tok" type="password" value={t} onChange={(e) => setT(e.target.value)} autoComplete="current-password" aria-describedby="tok-hint" />
          <p id="tok-hint" className="hint">
            Your college admin gives you this once. Volunteers get one too.
          </p>
          <div className="row">
            <button className="btn primary" type="submit">
              Sign in
            </button>
          </div>
        </form>

        <details style={{ marginTop: 30 }}>
          <summary style={{ fontWeight: 800, cursor: "pointer", minHeight: 44 }}>Setting up Saathi Campus for the first time?</summary>
          {newToken ? (
            <div className="ok-box">
              <p>Your admin token is below. Copy it somewhere safe now: it is shown only once.</p>
              <p style={{ wordBreak: "break-all", fontWeight: 800 }}>{newToken}</p>
              <button className="btn primary" onClick={() => onToken(newToken)}>
                Continue to the desk
              </button>
            </div>
          ) : (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  const r = await api("/api/setup/bootstrap", { json: setup });
                  setNewToken(r.admin_token);
                } catch (err) {
                  setSetupErr(errText(err));
                }
              }}
            >
              <label htmlFor="cc">College code (short, for students to type)</label>
              <input id="cc" type="text" value={setup.college_code} onChange={(e) => setSetup({ ...setup, college_code: e.target.value })} required />
              <label htmlFor="cn">College name</label>
              <input id="cn" type="text" value={setup.college_name} onChange={(e) => setSetup({ ...setup, college_name: e.target.value })} required />
              <label htmlFor="an">Your name</label>
              <input id="an" type="text" value={setup.admin_name} onChange={(e) => setSetup({ ...setup, admin_name: e.target.value })} required />
              <label htmlFor="ao">Your office (shown to students as who approved a notice)</label>
              <input id="ao" type="text" value={setup.admin_office} onChange={(e) => setSetup({ ...setup, admin_office: e.target.value })} />
              {setupErr ? <p className="notice-box" role="alert">{setupErr}</p> : null}
              <div className="row">
                <button className="btn primary" type="submit">
                  Set up college
                </button>
              </div>
            </form>
          )}
        </details>
      </main>
    </>
  );
}

/* ------------------------------------------------------------------ notices */

const STAGE: Record<string, string> = {
  queued: "Waiting",
  processing: "Reading",
  needs_review: "Needs your check",
  approved: "Publishing",
  published: "Published",
  superseded: "Replaced by newer version",
  rejected: "Rejected",
  failed: "Failed",
};

function Notices({ token, canPublish }: { token: string; canPublish: boolean }) {
  const [list, setList] = useState<any[]>([]);
  const [sel, setSel] = useState<string | null>(null);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const [lang, setLang] = useState("mr");
  const [tags, setTags] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    const r = await api("/api/notices", { staff: token });
    setList(r.notices);
    return r.notices as any[];
  }, [token]);

  useEffect(() => {
    load().catch((e) => setStatus({ text: errText(e), tone: "error" }));
  }, [load]);

  // Poll while anything is being read or published.
  useEffect(() => {
    if (!list.some((n) => ["queued", "processing", "approved"].includes(n.status))) return;
    const id = setInterval(() => load().catch(() => null), 3000);
    return () => clearInterval(id);
  }, [list, load]);

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    const file = fileRef.current?.files?.[0];
    if (!file) return setStatus({ text: "Choose the notice file first (PDF or photo).", tone: "error" });
    const f = new FormData();
    f.append("file", await shrinkImage(file, 2400, 0.9)); // notice photos keep more detail than board photos; PDFs pass through untouched
    f.append("language_hint", lang);
    if (tags.trim()) f.append("audience_tags", tags);
    setStatus({ text: "Uploading…" });
    try {
      const r = await api("/api/notices", { staff: token, form: f, timeoutMs: 120000 });
      setStatus({ text: r.duplicate ? "This file was already uploaded. Opening it." : "Uploaded. Reading the notice now; it will be ready for your check in about half a minute.", tone: "ok" });
      if (fileRef.current) fileRef.current.value = "";
      await load();
      setSel(r.notice.id);
    } catch (err) {
      setStatus({ text: errText(err), tone: "error" });
    }
  };

  return (
    <div className="split">
      <div>
        <h2>Upload a notice</h2>
        <form onSubmit={upload}>
          <label htmlFor="nf">Notice file (PDF or photo)</label>
          <input id="nf" ref={fileRef} type="file" accept="application/pdf,image/*" />
          <label htmlFor="nl">Main language of the notice</label>
          <select id="nl" value={lang} onChange={(e) => setLang(e.target.value)}>
            <option value="mr">Marathi</option>
            <option value="hi">Hindi</option>
            <option value="en">English</option>
          </select>
          <label htmlFor="nt">Only for these classes (optional)</label>
          <input id="nt" type="text" value={tags} onChange={(e) => setTags(e.target.value)} placeholder="FY-BCOM, SY-BCOM" aria-describedby="nt-hint" />
          <p id="nt-hint" className="hint">
            Leave empty to send to every student.
          </p>
          <div className="row">
            <button className="btn primary" type="submit">
              Upload and read
            </button>
          </div>
        </form>
        <p role="status" aria-live="polite" className={status.tone === "error" ? "notice-box" : status.tone === "ok" ? "ok-box" : "muted"} hidden={!status.text}>
          {status.text}
        </p>

        <h2 style={{ marginTop: 28 }}>Notices</h2>
        {list.length === 0 ? <p className="muted">No notices yet.</p> : null}
        <ul className="list queue">
          {list.map((n) => (
            <li key={n.id} style={{ padding: 0 }}>
              <button className="item" aria-current={sel === n.id} onClick={() => setSel(n.id)}>
                <span className="title">{n.title || "Untitled notice"}</span>
                <br />
                <span className={n.status === "needs_review" ? "tag warn" : n.status === "published" ? "tag ok" : "small muted"}>{STAGE[n.status] || n.status}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div>{sel ? <NoticeDetail key={sel} id={sel} token={token} canPublish={canPublish} onChanged={load} /> : <p className="muted">Choose a notice to check and publish it.</p>}</div>
    </div>
  );
}

function NoticeDetail({ id, token, canPublish, onChanged }: { id: string; token: string; canPublish: boolean; onChanged: () => void }) {
  const [n, setN] = useState<any>(null);
  const [src, setSrc] = useState<{ url: string; pdf: boolean } | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const headRef = useRef<HTMLHeadingElement>(null);

  const load = useCallback(async () => setN(await api(`/api/notices/${id}`, { staff: token })), [id, token]);
  useEffect(() => {
    load().catch((e) => setMsg({ text: errText(e), tone: "error" }));
  }, [load]);
  useEffect(() => {
    if (!n || !["queued", "processing", "approved"].includes(n.status)) return;
    const t = setInterval(() => load().catch(() => null), 3000);
    return () => clearInterval(t);
  }, [n, load]);
  useEffect(() => {
    let url = "";
    fetch(`/api/notices/${id}/source`, { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => (r.ok ? r.blob() : null))
      .then((b) => {
        if (!b) return;
        url = URL.createObjectURL(b);
        setSrc({ url, pdf: b.type === "application/pdf" });
      })
      .catch(() => null);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, token]);

  if (!n) return <p role="status">Loading…</p>;
  const flagged = (n.actions || []).filter((a: any) => a.verification === "needs_check");
  const allChecked = flagged.every((a: any) => checked.has(a.id));

  const approve = async () => {
    try {
      await api(`/api/notices/${id}/approve`, { staff: token, json: { confirmed_action_ids: [...checked] } });
      setMsg({ text: "Approved. Sending voice notes and messages to students now.", tone: "ok" });
      await load();
      onChanged();
      headRef.current?.focus();
    } catch (e) {
      setMsg({ text: errText(e), tone: "error" });
    }
  };
  const reject = async () => {
    const note = window.prompt("Why is this notice rejected? (shown in the log)") || "";
    if (note.trim().length < 3) return;
    try {
      await api(`/api/notices/${id}/reject`, { staff: token, json: { note } });
      setMsg({ text: "Rejected. Nothing was sent to students." });
      await load();
      onChanged();
    } catch (e) {
      setMsg({ text: errText(e), tone: "error" });
    }
  };

  return (
    <article aria-labelledby="nd-h">
      <h2 id="nd-h" ref={headRef} tabIndex={-1}>
        {n.title || "Reading the notice…"}
      </h2>
      <p>
        <span className={n.status === "needs_review" ? "tag warn" : n.status === "published" ? "tag ok" : "tag"}>{STAGE[n.status] || n.status}</span>{" "}
        {n.issuer ? <span className="muted">{n.issuer}</span> : null}
      </p>
      {n.error_message ? <p className="notice-box">{n.error_message}</p> : null}

      {n.actions?.length ? (
        <>
          <h3>Dates and actions</h3>
          <p className="hint">Dates marked “check” did not match the scan (for example the weekday is wrong). Look at the original and confirm each one.</p>
          <table>
            <thead>
              <tr>
                <th scope="col">What</th>
                <th scope="col">When</th>
                <th scope="col">Check</th>
              </tr>
            </thead>
            <tbody>
              {n.actions.map((a: any) => (
                <tr key={a.id}>
                  <td>
                    <strong>{a.label}</strong>
                    {a.venue ? <div className="small muted">{a.venue}</div> : null}
                    {a.source_quote ? <div className="small muted">“{a.source_quote}”</div> : null}
                  </td>
                  <td>
                    {a.due_date_spoken || "No date"}
                    {a.due_time ? `, ${speakTime(a.due_time)}` : ""}
                  </td>
                  <td>
                    {a.verification === "needs_check" ? (
                      <>
                        <span className="tag warn">Check</span>
                        <ul className="small">
                          {a.verify_reasons.map((r: string) => (
                            <li key={r}>{r}</li>
                          ))}
                        </ul>
                        {n.status === "needs_review" ? (
                          <label className="check small">
                            <input
                              type="checkbox"
                              checked={checked.has(a.id)}
                              onChange={(e) => {
                                const s = new Set(checked);
                                e.target.checked ? s.add(a.id) : s.delete(a.id);
                                setChecked(s);
                              }}
                            />
                            I checked this date on the original
                          </label>
                        ) : null}
                      </>
                    ) : (
                      <span className="tag ok">Matches scan</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      ) : null}

      {n.summaries?.en ? (
        <>
          <h3>What students will hear</h3>
          <p>{n.summaries.en}</p>
          {n.summaries.mr ? <p lang="mr">{n.summaries.mr}</p> : null}
        </>
      ) : null}
      {n.renditions?.some((r: any) => r.audio_url) ? (
        <>
          <h3>Voice notes</h3>
          {n.renditions
            .filter((r: any) => r.audio_url)
            .map((r: any) => (
              <p key={r.lang}>
                <span className="small">{r.lang === "mr" ? "Marathi" : r.lang === "hi" ? "Hindi" : "English"}</span>
                <br />
                <audio controls preload="none" src={rel(r.audio_url)} />
              </p>
            ))}
        </>
      ) : null}

      <p role="status" aria-live="polite" className={msg.tone === "error" ? "notice-box" : msg.tone === "ok" ? "ok-box" : "muted"} hidden={!msg.text}>
        {msg.text}
      </p>
      {n.status === "needs_review" && canPublish ? (
        <div className="row">
          <button className="btn primary" onClick={approve} disabled={!allChecked} aria-describedby={!allChecked ? "approve-why" : undefined}>
            Approve and send to students
          </button>
          <button className="btn danger" onClick={reject}>
            Reject
          </button>
          {!allChecked ? (
            <p id="approve-why" className="hint" style={{ flexBasis: "100%" }}>
              Confirm every date marked “check” first.
            </p>
          ) : null}
        </div>
      ) : null}
      {n.status === "published" ? (
        <p>
          <a href={`/n/${n.public_id}`} target="_blank" rel="noreferrer">
            Open the accessible page students get (opens in a new tab)
          </a>
        </p>
      ) : null}

      {src ? (
        <>
          <h3>Original</h3>
          {src.pdf ? (
            <a href={src.url} target="_blank" rel="noreferrer">
              Open the original PDF (opens in a new tab)
            </a>
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img className="thumb" src={src.url} alt="The original notice as uploaded" />
          )}
        </>
      ) : null}
      {n.ocr_text ? (
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 700 }}>Text read from the scan</summary>
          <pre className="ocr">{n.ocr_text}</pre>
        </details>
      ) : null}
    </article>
  );
}

/* ------------------------------------------------------------------ walkthrough routes */

function Routes({ token, me }: { token: string; me: any }) {
  const [routes, setRoutes] = useState<any[]>([]);
  const [form, setForm] = useState({ title: "", from_place: "", to_place: "" });
  const [msg, setMsg] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const load = useCallback(async () => setRoutes((await api("/api/routes", { staff: token })).routes), [token]);
  useEffect(() => {
    load().catch((e) => setMsg({ text: errText(e), tone: "error" }));
  }, [load]);

  return (
    <div className="split">
      <div>
        <h2>New walkthrough</h2>
        <p className="hint">Walk the route yourself and record one short clip per step. Someone else checks it before students can play it.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            try {
              const r = await api("/api/routes", { staff: token, json: form });
              window.location.href = `/desk/routes/${r.id}`;
            } catch (err) {
              setMsg({ text: errText(err), tone: "error" });
            }
          }}
        >
          <label htmlFor="rt">Name</label>
          <input id="rt" type="text" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="Main gate to exam hall" required />
          <label htmlFor="rf">Starts at</label>
          <input id="rf" type="text" value={form.from_place} onChange={(e) => setForm({ ...form, from_place: e.target.value })} placeholder="Main gate" required />
          <label htmlFor="rto">Ends at</label>
          <input id="rto" type="text" value={form.to_place} onChange={(e) => setForm({ ...form, to_place: e.target.value })} placeholder="Exam hall A" required />
          <div className="row">
            <button className="btn primary" type="submit">
              Start recording
            </button>
          </div>
        </form>
        <p role="status" className={msg.tone === "error" ? "notice-box" : "muted"} hidden={!msg.text}>
          {msg.text}
        </p>
        <p className="small">
          Student list of published walkthroughs: <Link href={`/routes?c=${me.college.id}`}>open it</Link>.
        </p>
      </div>
      <div>
        <h2>Walkthroughs</h2>
        {routes.length === 0 ? <p className="muted">None yet.</p> : null}
        <ul className="list">
          {routes.map((r) => (
            <li key={r.id}>
              <Link className="title" href={`/desk/routes/${r.id}`}>
                {r.title}
              </Link>
              <br />
              <span className={r.status === "published" ? "tag ok" : "tag"}>{r.status === "published" ? "Live" : "Draft"}</span>{" "}
              <span className="small muted">
                {r.steps.length} steps, recorded by {r.recorded_by || "unknown"}
                {r.approved_by ? `, checked by ${r.approved_by}` : ""}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ students */

function Students({ token }: { token: string }) {
  const [list, setList] = useState<any[]>([]);
  const [f, setF] = useState({ phone: "", name: "", preferred_lang: "mr", tags: "", consent: false });
  const [result, setResult] = useState<any>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => setList((await api("/api/students", { staff: token })).students || []), [token]);
  useEffect(() => {
    load().catch(() => null);
  }, [load]);

  return (
    <div className="split">
      <div>
        <h2>Add a student</h2>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setMsg("");
            try {
              const r = await api("/api/students", {
                staff: token,
                json: { phone: f.phone, name: f.name || null, preferred_lang: f.preferred_lang, audience_tags: f.tags.split(",").map((x) => x.trim()).filter(Boolean), consent_confirmed: f.consent },
              });
              setResult(r);
              setF({ ...f, phone: "", name: "", consent: false });
              load();
            } catch (err) {
              setMsg(errText(err));
            }
          }}
        >
          <label htmlFor="sp">WhatsApp number</label>
          <input id="sp" type="tel" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} autoComplete="off" required />
          <label htmlFor="sn">Name (optional)</label>
          <input id="sn" type="text" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          <label htmlFor="sl">Language</label>
          <select id="sl" value={f.preferred_lang} onChange={(e) => setF({ ...f, preferred_lang: e.target.value })}>
            <option value="mr">Marathi</option>
            <option value="hi">Hindi</option>
            <option value="en">English</option>
          </select>
          <label htmlFor="st">Class (optional)</label>
          <input id="st" type="text" value={f.tags} onChange={(e) => setF({ ...f, tags: e.target.value })} placeholder="FY-BCOM" />
          <label className="check" style={{ marginTop: 16 }}>
            <input type="checkbox" checked={f.consent} onChange={(e) => setF({ ...f, consent: e.target.checked })} />
            The student agreed to get college notices on WhatsApp
          </label>
          <div className="row">
            <button className="btn primary" type="submit">
              Add student
            </button>
          </div>
        </form>
        {msg ? <p className="notice-box" role="alert">{msg}</p> : null}
        {result ? (
          <div className="ok-box" role="status">
            <p>Added {result.phone}. {result.note}</p>
            <p style={{ wordBreak: "break-all" }}>
              Student app link: <a href={new URL(result.app_link).pathname + new URL(result.app_link).search}>{result.app_link}</a>
            </p>
          </div>
        ) : null}
      </div>
      <div>
        <h2>Students ({list.length})</h2>
        <ul className="list">
          {list.map((s) => (
            <li key={s.id}>
              <span className="title">{s.name || "Student"}</span> <span className="muted">{s.phone}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ team (admin only) */

function Team({ token }: { token: string }) {
  const [list, setList] = useState<any[]>([]);
  const [f, setF] = useState({ name: "", role: "verifier", office: "", rotaract_club: "" });
  const [made, setMade] = useState<{ name: string; token: string } | null>(null);
  const [msg, setMsg] = useState("");
  const load = useCallback(async () => setList((await api("/api/staff", { staff: token })).staff || []), [token]);
  useEffect(() => {
    load().catch(() => null);
  }, [load]);
  return (
    <div className="split">
      <div>
        <h2>Add a team member</h2>
        <p className="hint">Volunteers can record walkthroughs and help check notices. Staff can also approve and publish.</p>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setMsg("");
            try {
              const r = await api("/api/staff", { staff: token, json: { name: f.name, role: f.role, office: f.office || null, rotaract_club: f.rotaract_club || null } });
              setMade({ name: f.name, token: r.token });
              setF({ ...f, name: "", office: "", rotaract_club: "" });
              load();
            } catch (err) {
              setMsg(errText(err));
            }
          }}
        >
          <label htmlFor="tn">Name</label>
          <input id="tn" type="text" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required />
          <label htmlFor="tr">Role</label>
          <select id="tr" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value })}>
            <option value="verifier">Rotaract volunteer</option>
            <option value="staff">College staff</option>
          </select>
          <label htmlFor="tc">Rotaract club (optional)</label>
          <input id="tc" type="text" value={f.rotaract_club} onChange={(e) => setF({ ...f, rotaract_club: e.target.value })} />
          <label htmlFor="to">Office (optional)</label>
          <input id="to" type="text" value={f.office} onChange={(e) => setF({ ...f, office: e.target.value })} />
          <div className="row">
            <button className="btn primary" type="submit">
              Create sign-in token
            </button>
          </div>
        </form>
        {msg ? <p className="notice-box" role="alert">{msg}</p> : null}
        {made ? (
          <div className="ok-box" role="status">
            <p>Sign-in token for {made.name}. Send it privately now: it is shown only once.</p>
            <p style={{ wordBreak: "break-all", fontWeight: 800 }}>{made.token}</p>
          </div>
        ) : null}
      </div>
      <div>
        <h2>Team ({list.length})</h2>
        <ul className="list">
          {list.map((m) => (
            <li key={m.id}>
              <span className="title">{m.name}</span> <span className="muted">{m.role === "verifier" ? "Rotaract volunteer" : m.role}{m.rotaract_club ? `, ${m.rotaract_club}` : ""}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
