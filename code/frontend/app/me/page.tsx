/** The student page (/me). A thin server page that sets the metadata and mounts the student app. */

import type { Metadata } from "next";
import StudentApp from "./StudentApp";

export const metadata: Metadata = { title: "Hear a notice", robots: { index: false } };

export default function Page() {
  return <StudentApp />;
}
