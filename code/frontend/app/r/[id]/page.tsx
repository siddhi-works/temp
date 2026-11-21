/** Public page for a campus walkthrough route, opened from a QR code. */

import { notFound } from "next/navigation";
import { COLLEGE_MODE } from "@/lib/features";
import RoutePlayer from "./RoutePlayer";

export const metadata = { title: "Walkthrough" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  if (!COLLEGE_MODE) notFound(); // the college side is switched off in the student-facing product
  const { id } = await params;
  return <RoutePlayer id={id} />;
}
