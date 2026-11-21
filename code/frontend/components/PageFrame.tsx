"use client";

/** Gives staff and public pages the same readable look as the student pages, using the colours and text size the person chose. */

import { useEffect } from "react";
import { clearDisplay, useDisplay } from "@/lib/display";
import "../app/me/me.css";

export default function PageFrame({ children }: { children: React.ReactNode }) {
  useDisplay(); // applies the saved colours, text size and spacing
  useEffect(() => () => clearDisplay(), []);
  return <div className="me">{children}</div>;
}
