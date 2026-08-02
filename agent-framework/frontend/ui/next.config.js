/** @type {import('next').NextConfig} */

const aguiBackend = process.env.AGUI_BACKEND_URL || "http://localhost:8002";
const a2aBackend = process.env.A2A_BACKEND_URL || "http://localhost:8001";

const nextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${aguiBackend}/api/:path*`,
      },
      {
        source: "/health",
        destination: `${aguiBackend}/health`,
      },
      {
        source: "/a2a/:path*",
        destination: `${a2aBackend}/:path*`,
      },
    ];
  },
  webpack: (config) => {
    // CopilotKit v2 subpath ships Tailwind v4 CSS that conflicts with our
    // Tailwind v3 PostCSS setup. We only use the useAgent hook (no UI from
    // v2), so skip its CSS entirely.
    config.module.rules.push({
      test: /node_modules[\\/]@copilotkit[\\/]react-core[\\/]dist[\\/]v2[\\/].*\.css$/,
      use: "null-loader",
    });
    return config;
  },
};

module.exports = nextConfig;
