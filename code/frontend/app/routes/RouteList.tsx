"use client";

/** Renders the list of campus walkthrough routes. */

import Link from "next/link";
import { useEffect, useState } from "react";
import Header from "@/components/Header";
import PageFrame from "@/components/PageFrame";
import { api, errText } from "@/lib/api";

export default function RouteList() {
  const [college, setCollege] = useState<string | null>(null);
  const [routes, setRoutes] = useState<any[] | null>(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    const c = new URLSearchParams(window.location.search).get("c");
    setCollege(c || "");
    if (c)
      api(`/api/public/colleges/${encodeURIComponent(c)}/routes`)
        .then((r) => setRoutes(r.routes))
        .catch((e) => setErr(errText(e)));
  }, []);

  return (
    <PageFrame>
      <Header />
      <main id="main" className="wrap">
        <h1>Campus walkthroughs</h1>
        <p className="lede">Step-by-step walking routes, recorded by volunteers and checked by your college.</p>
        {college === "" ? (
          <p>Scan the walkthrough QR code at your college gate to open the routes for your campus.</p>
        ) : null}
        {err ? <p className="notice-box">{err}</p> : null}
        {routes && routes.length === 0 ? <p className="muted">Your college has not published any walkthroughs yet.</p> : null}
        {routes && routes.length ? (
          <ul className="list">
            {routes.map((r) => (
              <li key={r.id}>
                <Link className="title" href={`/r/${r.id}`}>
                  {r.title}
                </Link>
                <br />
                <span className="muted">
                  From {r.from} to {r.to}, {r.steps.length} steps
                </span>
              </li>
            ))}
          </ul>
        ) : null}
      </main>
    </PageFrame>
  );
}
