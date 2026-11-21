/** Same-origin proxy to the FastAPI backend: no CORS, and student links (/n/..., /media/...) work on one domain. */
const BACKEND = process.env.BACKEND_URL || "http://localhost:8000";

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
