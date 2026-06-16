/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8002/api/:path*",
      },
      {
        source: "/health",
        destination: "http://localhost:8002/health",
      },
    ];
  },
};

module.exports = nextConfig;
