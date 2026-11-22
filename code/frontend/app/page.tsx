"use client";

/** The first page a visitor sees: what it does, why to trust it, a tour video, and one big button to open the app. */

import Logo from "@/components/Logo";
import { IconCheck, IconEar, IconShare, IconShield } from "@/components/Icons";
import { useT } from "@/lib/i18n";
import { Screen } from "./me/Shell";

export default function Home() {
  const { t } = useT();
  return (
    <Screen title={t("welcome_title")}>
      <div className="hero">
        <Logo />
        <p className="haiku"><span>{t("haiku_1")}</span><span>{t("haiku_2")}</span><span>{t("haiku_3")}</span></p>
      </div>
      <p>{t("welcome_lede")}</p>
      <div className="stack">
        <a className="big-btn main" href="/me">{t("open_app")}</a>
      </div>
      <p className="muted" style={{ marginTop: 18 }}>{t("welcome_privacy")}</p>

      <h2>{t("steps_h")}</h2>
      <ol className="steps">
        <li><span className="ic"><IconShare /></span>{t("step1")}</li>
        <li><span className="ic"><IconEar /></span>{t("step2")}</li>
        <li><span className="ic"><IconCheck /></span>{t("step3")}</li>
      </ol>

      <h2>{t("land_h")}</h2>
      <div className="features">
        <section className="card" aria-labelledby="f1"><div className="p-head"><IconCheck /><h3 id="f1">{t("land_proof_h")}</h3></div><p>{t("land_proof_t")}</p></section>
        <section className="card" aria-labelledby="f2"><div className="p-head"><IconShield /><h3 id="f2">{t("land_safety_h")}</h3></div><p>{t("land_safety_t")}</p></section>
        <section className="card" aria-labelledby="f3"><div className="p-head"><IconEar /><h3 id="f3">{t("land_wa_h")}</h3></div><p>{t("land_wa_t")}</p></section>
      </div>

      <p><a href="/about">{t("about_link")}</a></p>
    </Screen>
  );
}
