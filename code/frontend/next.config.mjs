/**
 * Next.js configuration.
 *  1. Same-origin proxy to the FastAPI backend: no CORS, and student links (/n/..., /media/...) work on one domain.
 *  2. Security headers for every page, including a strict Content Security Policy:
 *     only our own origin may load scripts, styles, fonts, audio and connections; nothing can frame the site.
 */
const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'", // Next.js inlines a small bootstrap script
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "media-src 'self' blob: data:", // the spoken notice is played from an in-memory blob
  "font-src 'self' data:",
  "connect-src 'self'",
  "worker-src 'self'", // service worker: share target and push alerts
  "manifest-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CSP },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), geolocation=(), microphone=(self)" },
  { key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" },
];

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${BACKEND}/api/:path*` },
      { source: "/media/:path*", destination: `${BACKEND}/media/:path*` },
      { source: "/n/:path*", destination: `${BACKEND}/n/:path*` },
      { source: "/webhooks/:path*", destination: `${BACKEND}/webhooks/:path*` }, // WhatsApp calls this, so one public URL is enough
      { source: "/health", destination: `${BACKEND}/health` },
    ];
  },
  async headers() {
    return [
      { source: "/:path*", headers: SECURITY_HEADERS },
      {
        source: "/sw.js",
        headers: [
          { key: "Service-Worker-Allowed", value: "/" },
          { key: "Cache-Control", value: "no-cache" },
        ],
      },
    ];
  },
};

export default nextConfig;
