"use client";

/** 'Skip to main content' link, shown in the language the student chose. */

import { useT } from "@/lib/i18n";

/**
 * "Skip to main content", in the language the student chose.
 * It moves focus with script instead of following `#main`: the student app uses the address hash as its screen
 * router (#read, #settings ...), so a plain `#main` link sent the student back to the home screen.
 */
export default function SkipLink() {
  const { t } = useT();
  return (
    <a
      className="skip"
      href="#main"
      onClick={(e) => {
        const main = document.getElementById("main");
        if (!main) return;
        e.preventDefault();
        main.setAttribute("tabindex", "-1");
        main.focus();
        main.scrollIntoView?.();
      }}
    >
      {t("skip")}
    </a>
  );
}
