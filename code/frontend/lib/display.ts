"use client";

/** Display choices (colours, text size, letter spacing): the student decides, and it is remembered on this phone only. */
/** Display choices (colours, text size, letter spacing): the student decides, and it is remembered on this phone only. */
import { useEffect, useState } from "react";

export type Look = "daylight" | "sepia" | "dark" | "contrast";
export type Size = "normal" | "large" | "xlarge";
export type Space = "normal" | "wide";
type Prefs = { look: Look; size: Size; space: Space };

let prefs: Prefs | null = null;
const subs = new Set<() => void>();

function initial(): Prefs {
  let saved: Partial<Prefs> = {};
  try {
    saved = JSON.parse(localStorage.getItem("saathi_display") || "{}");
  } catch {}
  const mq = (q: string) => typeof window !== "undefined" && window.matchMedia?.(q).matches;
  const auto: Look = mq("(prefers-contrast: more)") ? "contrast" : mq("(prefers-color-scheme: dark)") ? "dark" : "daylight";
  const known = ["daylight", "sepia", "dark", "contrast"];
  return { look: known.includes(saved.look as string) ? (saved.look as Look) : auto, size: saved.size || "normal", space: saved.space || "normal" };
}

function apply(p: Prefs) {
  const el = document.documentElement;
  el.dataset.look = p.look;
  el.dataset.size = p.size;
  el.dataset.space = p.space;
}

export function useDisplay() {
  const [, bump] = useState(0);
  useEffect(() => {
    if (!prefs) {
      prefs = initial();
      apply(prefs);
    }
    bump((n) => n + 1);
    const f = () => bump((n) => n + 1);
    subs.add(f);
    return () => {
      subs.delete(f);
    };
  }, []);
  const set = (patch: Partial<Prefs>) => {
    prefs = { ...(prefs || initial()), ...patch };
    apply(prefs);
    try {
      localStorage.setItem("saathi_display", JSON.stringify(prefs));
    } catch {}
    subs.forEach((f) => f());
  };
  return { prefs: prefs || { look: "daylight" as Look, size: "normal" as Size, space: "normal" as Space }, set };
}

/** Remove the attributes when leaving the student pages, so the staff pages keep their own look. */
export function clearDisplay() {
  const el = document.documentElement;
  delete el.dataset.look;
  delete el.dataset.size;
  delete el.dataset.space;
  prefs = null;
}
