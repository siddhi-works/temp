"use client";

/** 'Skip to main content' link, shown in the language the student chose. */

import { useT } from "@/lib/i18n";

/** "Skip to main content", in the language the student chose. */
export default function SkipLink() {
  const { t } = useT();
  return (
    <a className="skip" href="#main">
      {t("skip")}
    </a>
  );
}
