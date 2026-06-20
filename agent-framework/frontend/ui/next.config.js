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
      {
        source: "/a2a/:path*",
        destination: "http://localhost:8001/:path*",
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
