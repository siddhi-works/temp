"use client";

/** Settings screen: 'My details' (kept on this phone only) and the display choices (text size, colours, spacing). */

import { useEffect, useRef, useState } from "react";
import { useDisplay, type Look, type Size, type Space } from "@/lib/display";
import { useT } from "@/lib/i18n";
import { Status } from "./Shell";

const EMPTY = { name: "", course: "", division: "", roll_no: "", batch: "", extra: "" };
const FIELDS = [
  ["name", "f_name"],
  ["course", "f_course"],
  ["division", "f_division"],
  ["roll_no", "f_roll"],
  ["batch", "f_batch"],
  ["extra", "f_extra"],
] as const;

function Choice<T extends string>({ label, value, options, onPick }: { label: string; value: T; options: [T, string][]; onPick: (v: T) => void }) {
  return (
    <div role="group" aria-label={label}>
      <h3>{label}</h3>
      <div className="seg">
        {options.map(([v, text]) => (
          <button key={v} type="button" aria-pressed={value === v} onClick={() => onPick(v)}>{text}</button>
        ))}
      </div>
    </div>
  );
}

/** My details (kept only on this phone), display choices, and any extra settings passed in. */
export default function SettingsScreen({ extra }: { extra?: React.ReactNode }) {
  const { t } = useT();
  const { prefs, set } = useDisplay();
  const [profile, setProfile] = useState(EMPTY);
  const ref = useRef(profile);
  ref.current = profile;
  const [msg, setMsg] = useState("");

  useEffect(() => {
    try {
      const raw = localStorage.getItem("saathi_profile");
      if (raw) setProfile({ ...EMPTY, ...JSON.parse(raw) });
    } catch {}
  }, []);

  const edit = (k: keyof typeof EMPTY, v: string) => {
    const next = { ...ref.current, [k]: v };
    setProfile(next);
    setMsg("");
    try {
      localStorage.setItem("saathi_profile", JSON.stringify(next));
    } catch {}
  };
  const forget = () => {
    if (!window.confirm(t("forget_confirm"))) return;
    setProfile(EMPTY);
    try {
      localStorage.removeItem("saathi_profile");
    } catch {}
    setMsg(t("forgot"));
  };

  return (
    <div>
      <section aria-labelledby="det-h">
        <h2 id="det-h" style={{ marginTop: 0 }}>{t("my_details")}</h2>
        <p className="muted">{t("details_note")}</p>
        {FIELDS.map(([k, label]) => (
          <div key={k}>
            <label htmlFor={`prof-${k}`}>{t(label)}</label>
            <input id={`prof-${k}`} type="text" autoComplete={k === "name" ? "name" : "off"} maxLength={k === "extra" ? 120 : 60} value={profile[k]} onChange={(e) => edit(k, e.target.value)} aria-describedby={k === "roll_no" ? "prn-help" : undefined} />
            {k === "roll_no" ? <p id="prn-help" className="muted" style={{ marginTop: 8 }}>{t("prn_help")}</p> : null}
          </div>
        ))}
        <div className="stack" style={{ marginTop: 24 }}>
          <button type="button" className="big-btn" onClick={forget}>{t("forget")}</button>
        </div>
        <Status text={msg} tone="ok" />
      </section>

      <section aria-labelledby="disp-h">
        <h2 id="disp-h">{t("display")}</h2>
        <Choice<Size> label={t("text_size")} value={prefs.size} onPick={(size) => set({ size })} options={[["normal", t("size_normal")], ["large", t("size_large")], ["xlarge", t("size_xlarge")]]} />
        <Choice<Look> label={t("colours")} value={prefs.look} onPick={(look) => set({ look })} options={[["daylight", t("look_daylight")], ["sepia", t("look_sepia")], ["dark", t("look_dark")], ["contrast", t("look_contrast")]]} />
        <Choice<Space> label={t("spacing")} value={prefs.space} onPick={(space) => set({ space })} options={[["normal", t("space_normal")], ["wide", t("space_wide")]]} />
      </section>

      {extra}
    </div>
  );
}
