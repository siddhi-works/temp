"use client";

/** First screen for a new student: one Start button, no account. Creates an anonymous session. */

import { useState } from "react";
import { api } from "@/lib/api";
import { friendlyError, useT } from "@/lib/i18n";
import { Screen, Status } from "./Shell";

export default function Welcome({ onToken, expired }: { onToken: (t: string) => void; expired?: boolean }) {
  const { t, lang } = useT();
  const [code, setCode] = useState("");
  const [showCode, setShowCode] = useState(false);
  const [busy, setBusy] = useState(false);
  const [waking, setWaking] = useState(false); // free hosting sleeps when idle; say so instead of looking frozen
  const [err, setErr] = useState("");
  const go = async () => {
    setBusy(true);
    setErr("");
    const timer = setTimeout(() => setWaking(true), 4000);
    try {
      const r = await api("/api/public/join", { json: { lang, code: code.trim() || undefined }, timeoutMs: 100000 });
      onToken(r.token);
    } catch (e) {
      setErr(friendlyError(e, t));
      if ((e as { code?: string })?.code === "access_code_required") setShowCode(true);
      setBusy(false);
    } finally {
      clearTimeout(timer);
      setWaking(false);
    }
  };
  return (
    <Screen title={t("welcome_title")}>
      <p>{t("welcome_lede")}</p>
      {expired ? <p className="notice-box">{t("session_expired")}</p> : null}
      <details open={showCode} onToggle={(e) => setShowCode((e.currentTarget as HTMLDetailsElement).open)} style={{ margin: "20px 0" }}>
        <summary>{t("access_toggle")}</summary>
        <label htmlFor="welcome-code">{t("access_code")}</label>
        <input id="welcome-code" type="text" autoComplete="off" autoCapitalize="none" value={code} onChange={(e) => setCode(e.target.value)} />
      </details>
      <div className="stack" style={{ marginTop: 24 }}>
        <button type="button" className="big-btn main" onClick={go} disabled={busy}>
          {busy ? t("starting") : t("start")}
        </button>
      </div>
      <Status text={waking ? t("waking") : err} tone={waking ? undefined : "error"} />
      <p className="muted" style={{ marginTop: 24 }}>{t("welcome_privacy")}</p>
    </Screen>
  );
}
