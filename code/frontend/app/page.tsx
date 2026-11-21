"use client";

/** Landing page: one sentence on what Saathi does, one big button to open it, and a link to how it works. */

import { useT } from "@/lib/i18n";
import { Screen } from "./me/Shell";

export default function Home() {
  const { t } = useT();
  return (
    <Screen title={t("welcome_title")}>
      <p>{t("welcome_lede")}</p>
      <div className="stack">
        <a className="big-btn main" href="/me">
          {t("open_app")}
        </a>
      </div>
      <p style={{ marginTop: 32 }}>
        <a href="/about">{t("about_link")}</a>
      </p>
      <p className="muted">{t("welcome_privacy")}</p>
    </Screen>
  );
}
