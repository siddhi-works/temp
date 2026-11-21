/** Staff page for one campus walkthrough route. */

import { notFound } from "next/navigation";
import { COLLEGE_MODE } from "@/lib/features";
import Recorder from "./Recorder";

export const metadata = { title: "Record a walkthrough", robots: { index: false } };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  if (!COLLEGE_MODE) notFound(); // the college side is switched off in the student-facing product
  const { id } = await params;
  return <Recorder id={id} />;
}
