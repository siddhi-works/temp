"use client";

/** About page: what Samyukta does, what it stores, which AI tools it uses, and its accessibility statement (GIGW 3.0 expects one). */

import { useT } from "@/lib/i18n";
import { Screen } from "../me/Shell";

const SECTIONS: [string, string][] = [
  ["ab_what_h", "ab_what"],
  ["ab_priv_h", "ab_priv"],
  ["ab_ai_h", "ab_ai"],
  ["ab_acc_h", "ab_acc"],
  ["ab_limits_h", "ab_limits"],
];

/** Plain-language statement of what Samyukta does, what it stores, which AI tools it uses and how accessible it is. */
export default function About() {
  const { t } = useT();
  const email = process.env.NEXT_PUBLIC_CONTACT_EMAIL;
  return (
    <Screen title={t("ab_title")} onBack={() => (window.location.href = "/me")}>
      <p className="muted">{t("ab_event")}</p>
      {SECTIONS.map(([h, p]) => (
        <section key={h} aria-labelledby={h}>
          <h2 id={h}>{t(h)}</h2>
          <p>{t(p)}</p>
        </section>
      ))}
      <section aria-labelledby="ab_feedback_h">
        <h2 id="ab_feedback_h">{t("ab_feedback_h")}</h2>
        <p>{t("ab_feedback")}</p>
        {email ? (
          <p>
            <a href={`mailto:${email}`}>{email}</a>
          </p>
        ) : null}
      </section>
    </Screen>
  );
}
