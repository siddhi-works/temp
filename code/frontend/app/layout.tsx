import type { Metadata, Viewport } from "next";
import "@fontsource/atkinson-hyperlegible-next/latin-400.css";
import "@fontsource/atkinson-hyperlegible-next/latin-700.css";
import "@fontsource/atkinson-hyperlegible-next/latin-800.css";
// Devanagari files (about 50 KB each) are downloaded only on a page that actually shows Marathi or Hindi text.
import "@fontsource/noto-sans-devanagari/devanagari-400.css";
import "@fontsource/noto-sans-devanagari/devanagari-700.css";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Saathi Campus", template: "%s | Saathi Campus" },
  description: "College life for blind and low-vision students: notices, timetable clashes, deadlines said in class, board photos and campus routes, by voice.",
  manifest: "/manifest.webmanifest",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
    { media: "(prefers-color-scheme: dark)", color: "#0e1036" },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip" href="#main">
          Skip to main content
        </a>
        {children}
      </body>
    </html>
  );
}
