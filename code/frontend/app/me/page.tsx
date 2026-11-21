import type { Metadata } from "next";
import StudentApp from "./StudentApp";

export const metadata: Metadata = { title: "My campus", robots: { index: false } };

export default function Page() {
  return <StudentApp />;
}
