/** List of approved campus walkthrough routes. */

import { notFound } from "next/navigation";
import { COLLEGE_MODE } from "@/lib/features";
import RouteList from "./RouteList";

export const metadata = { title: "Walkthroughs" };

export default function Page() {
  if (!COLLEGE_MODE) notFound(); // the college side is switched off in the student-facing product
  return <RouteList />;
}
