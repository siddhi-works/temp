"use client";

import { useState } from "react";
import { api, errText } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { Screen, Status } from "./Shell";

export default function Welcome({ onToken, expired }: { onToken: (t: string) => void; expired?: boolean }) {
  const { t, lang } = useT();
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const go = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api("/api/public/join", { json: { lang, code: code.trim() || undefined } });
      onToken(r.token);
    } catch (e) {
      setErr(errText(e));
      setBusy(false);
    }
  };
  return (
    <Screen title={t("welcome_title")}>
      <p>{t("welcome_lede")}</p>
      {expired ? <p className="notice-box">{t("session_expired")}</p> : null}
      <label htmlFor="welcome-code">{t("access_code")}</label>
      <input id="welcome-code" type="text" autoComplete="off" autoCapitalize="none" value={code} onChange={(e) => setCode(e.target.value)} />
      <div className="stack" style={{ marginTop: 24 }}>
        <button type="button" className="big-btn main" onClick={go} disabled={busy}>
          {busy ? t("starting") : t("start")}
        </button>
      </div>
      <Status text={err} tone="error" />
      <p className="muted" style={{ marginTop: 24 }}>{t("welcome_privacy")}</p>
    </Screen>
  );
}
