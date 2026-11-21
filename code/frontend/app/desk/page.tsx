/** Staff desk page (/desk): mounts the desk app. */

import { notFound } from "next/navigation";
import { COLLEGE_MODE } from "@/lib/features";
import Desk from "./Desk";

export const metadata = { title: "College desk", robots: { index: false } };

export default function Page() {
  if (!COLLEGE_MODE) notFound(); // the college side is switched off in the student-facing product
  return <Desk />;
}
