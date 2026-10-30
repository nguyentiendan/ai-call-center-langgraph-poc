import type { NextConfig } from "next";

const proxy = process.env.API_PROXY_TARGET;
const nextConfig: NextConfig = {
  output: "standalone",
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    if (!proxy) return [];
    return [
      { source: "/api/:path*", destination: `${proxy}/api/:path*` },
      { source: "/health", destination: `${proxy}/health` },
      { source: "/readiness", destination: `${proxy}/readiness` },
    ];
  },
};
export default nextConfig;
