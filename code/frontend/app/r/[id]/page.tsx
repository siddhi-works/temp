/** Public page for a campus walkthrough route, opened from a QR code. */

import RoutePlayer from "./RoutePlayer";

export const metadata = { title: "Walkthrough" };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <RoutePlayer id={id} />;
}
