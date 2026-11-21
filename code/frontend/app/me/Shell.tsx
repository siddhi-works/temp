"use client";

import { useEffect, useRef } from "react";
import { LANGS, useT } from "@/lib/i18n";
import "./me.css";

/** Small status line announced politely by screen readers. */
export function Status({ text, tone }: { text: string; tone?: "error" | "ok" }) {
  return (
    <p role="status" aria-live="polite" className={tone === "error" ? "notice-box" : tone === "ok" ? "ok-box" : "muted"} hidden={!text}>
      {text}
    </p>
  );
}

export function speakWithPhone(text: string, lang: string) {
  if (!("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = { en: "en-IN", hi: "hi-IN", mr: "mr-IN" }[lang] || "en-IN";
  u.rate = 0.95;
  window.speechSynthesis.speak(u);
}

export function TopBar() {
  const { t, lang, setLang } = useT();
  return (
    <header className="topbar">
      <a className="brand-name" href="/me#" aria-label="Saathi, home">Saathi</a>
      <div className="langs" role="group" aria-label={t("language")}>
        {LANGS.map((l) => (
          <button key={l.code} type="button" lang={l.code} aria-pressed={lang === l.code} onClick={() => setLang(l.code)}>
            {l.name}
          </button>
        ))}
      </div>
    </header>
  );
}

/** One screen: Back first, then a title that receives focus so the screen reader announces where you are. */
export function Screen({ title, onBack, children }: { title: string; onBack?: () => void; children: React.ReactNode }) {
  const { t } = useT();
  const h1 = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    h1.current?.focus();
    window.scrollTo(0, 0);
  }, [title]);
  return (
    <div className="me">
      <title>{`${title} | Saathi`}</title>
      <TopBar />
      <main id="main" className="wrap">
        {onBack ? (
          <button type="button" className="back" onClick={onBack}>
            {t("back")}
          </button>
        ) : null}
        <h1 ref={h1} tabIndex={-1}>
          {title}
        </h1>
        {children}
      </main>
    </div>
  );
}
