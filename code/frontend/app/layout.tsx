/** Root layout: fonts, page metadata, the skip link, and the global stylesheet. Every page is rendered inside this. */

import type { Metadata, Viewport } from "next";
import "@fontsource/atkinson-hyperlegible-next/latin-400.css";
import "@fontsource/atkinson-hyperlegible-next/latin-700.css";
import "@fontsource/atkinson-hyperlegible-next/latin-800.css";
// Devanagari files (about 50 KB each) are downloaded only on a page that actually shows Marathi or Hindi text.
import "@fontsource/noto-sans-devanagari/devanagari-400.css";
import "@fontsource/noto-sans-devanagari/devanagari-700.css";
import SkipLink from "@/components/SkipLink";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Samyukta", template: "%s | Samyukta" },
  description: "Hear any college notice read aloud and checked, in English, Hindi and Marathi. Made for blind, low-vision and dyslexic students.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon.svg" },
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
        <SkipLink />
        {children}
      </body>
    </html>
  );
}
