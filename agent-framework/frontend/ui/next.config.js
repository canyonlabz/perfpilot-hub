/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/copilotkit/:path*",
        destination: "http://localhost:8002/copilotkit/:path*",
      },
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
