import Recorder from "./Recorder";

export const metadata = { title: "Record a walkthrough", robots: { index: false } };

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <Recorder id={id} />;
}
