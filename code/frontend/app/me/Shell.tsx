"use client";

/** Shared page frame for every student screen: top bar with language buttons, Back button, and a focused page title. */

import { useEffect, useRef } from "react";
import Logo from "@/components/Logo";
import { useDisplay } from "@/lib/display";
import { LANGS, useT } from "@/lib/i18n";
import "./me.css";

/**
 * Status line announced by screen readers (TalkBack, VoiceOver, NVDA).
 * The two live regions are ALWAYS in the page and only their content changes. A region that is created or un-hidden
 * at the same moment as its text (`hidden`, `display: none`, or mounted together with the message) is usually not
 * announced at all, which is how results and errors went unheard. Errors use the assertive region.
 */
export function Status({ text, tone }: { text: string; tone?: "error" | "ok" }) {
  const isError = tone === "error";
  return (
    <>
      <div className="live" role="status" aria-live="polite" aria-atomic="true">
        {text && !isError ? <p className={tone === "ok" ? "ok-box" : "muted"}>{text}</p> : null}
      </div>
      <div className="live" role="alert" aria-live="assertive" aria-atomic="true">
        {text && isError ? <p className="notice-box">{text}</p> : null}
      </div>
    </>
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
      <a className="brand-name" href="/me#" aria-label={t("go_home")}><Logo />Samyukta</a>
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

/**
 * One screen: Back first, then a title that receives focus so the screen reader announces where you are.
 * Focus moves when the screen is opened, not when its title text changes: switching the interface language
 * must not throw the student's focus away from the language button they just pressed.
 * The caller gives each screen its own `key`, so opening another screen mounts a fresh one.
 */
export function Screen({ title, onBack, children }: { title: string; onBack?: () => void; children: React.ReactNode }) {
  const { t } = useT();
  useDisplay(); // apply the colours, text size and spacing the student saved, on every screen (not only in Settings)
  const h1 = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    h1.current?.focus();
    window.scrollTo(0, 0);
  }, []);
  return (
    <div className="me">
      <title>{`${title} | Samyukta`}</title>
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
