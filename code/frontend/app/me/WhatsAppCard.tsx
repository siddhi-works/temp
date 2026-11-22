"use client";

/**
 * Connect WhatsApp (Settings). The student scans a QR code (or presses Open WhatsApp), sends the message that is already typed,
 * and WhatsApp replies with a 4-digit code that is typed here. A phone number alone never links an account.
 * The QR holds only Samyukta's public number and a random one-time code.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { friendlyError, useT } from "@/lib/i18n";
import { Status } from "./Shell";

type State = { available: boolean; linked: boolean; number: string | null; waiting: boolean; claimed_by_last4?: string | null; open_url: string | null };
type Link = { url: string; qr_svg: string; code: string };

export default function WhatsAppCard({ token }: { token: string }) {
  const { t } = useT();
  const [st, setSt] = useState<State | null>(null);
  const [link, setLink] = useState<Link | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; tone?: "error" | "ok" }>({ text: "" });
  const codeRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setSt(await api<State>("/api/me/whatsapp", { token }));
    } catch {
      setSt(null);
    }
  }, [token]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // While a connection is waiting, look for the WhatsApp message every few seconds (stops after 10 minutes).
  useEffect(() => {
    if (!link || st?.linked) return;
    const started = Date.now();
    const id = setInterval(() => {
      if (Date.now() - started > 600000) {
        clearInterval(id);
        setLink(null);
        setStatus({ text: t("wa_expired"), tone: "error" });
        return;
      }
      refresh();
    }, 3000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [link, st?.linked, refresh]);

  const claimed = !!st?.claimed_by_last4;
  useEffect(() => {
    if (claimed) codeRef.current?.focus();
  }, [claimed]);

  if (!st || !st.available) return null; // no WhatsApp number on this server: the card does not exist

  const start = async () => {
    setBusy(true);
    setStatus({ text: "" });
    try {
      setLink(await api<Link>("/api/me/whatsapp/link", { token, method: "POST" }));
      setStatus({ text: t("wa_waiting") });
    } catch (e) {
      setStatus({ text: friendlyError(e, t), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const confirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/me/whatsapp/confirm", { token, json: { code } });
      setLink(null);
      setCode("");
      await refresh();
      setStatus({ text: "", tone: "ok" });
    } catch (err) {
      setStatus({ text: friendlyError(err, t), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const disconnect = async () => {
    if (!window.confirm(t("wa_disc_confirm"))) return;
    setBusy(true);
    try {
      await api("/api/me/whatsapp", { token, method: "DELETE" });
      await refresh();
      setStatus({ text: t("wa_disc_done"), tone: "ok" });
    } catch (err) {
      setStatus({ text: friendlyError(err, t), tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card" aria-labelledby="wa-h">
      <h3 id="wa-h">{t("wa_h")}</h3>
      {st.linked ? (
        <div className="stack">
          <p>{t("wa_done").replace("{n}", st.number || "")}</p>
          <button type="button" className="big-btn" disabled={busy} onClick={disconnect}>{t("wa_disconnect")}</button>
        </div>
      ) : (
        <div className="stack">
          <p>{t("wa_intro")}</p>
          {!link ? (
            <button type="button" className="big-btn main" disabled={busy} onClick={start}>{t("wa_connect")}</button>
          ) : (
            <>
              <p>{t("wa_steps")}</p>
              <a className="big-btn main" href={link.url} target="_blank" rel="noopener noreferrer">{t("wa_open")}</a>
              {/* White background and a quiet zone so a camera can read the code in every colour theme. */}
              <div style={{ background: "#fff", padding: 12, borderRadius: 12, width: "fit-content" }}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img alt={t("wa_qr_alt")} width={220} height={220} src={`data:image/svg+xml;utf8,${encodeURIComponent(link.qr_svg)}`} />
              </div>
              <p className="muted">{t("wa_code_label")} <strong>{link.code}</strong></p>
              {claimed ? (
                <form onSubmit={confirm} className="stack">
                  <p className="notice-box">{t("wa_claimed").replace("{n}", st.claimed_by_last4 || "")}</p>
                  <label htmlFor="wa-code">{t("wa_confirm_label")}</label>
                  <input
                    id="wa-code" ref={codeRef} type="text" inputMode="numeric" autoComplete="one-time-code" maxLength={4} pattern="[0-9]{4}"
                    value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} required
                  />
                  <button type="submit" className="big-btn main" disabled={busy || code.length !== 4}>{t("wa_confirm")}</button>
                </form>
              ) : null}
            </>
          )}
        </div>
      )}
      <Status text={status.text} tone={status.tone} />
    </section>
  );
}
