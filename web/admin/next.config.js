/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    typedRoutes: false,
  },
  async rewrites() {
    const backend =
      process.env.NEXT_PUBLIC_API_URL ??
      process.env.BACKEND_URL ??
      "http://127.0.0.1:8000";
    return [
      // Google OAuth is deliberately not proxied here: the platform console only
      // accepts a password sign-in, so the SSO flow is unreachable from this origin.
      {
        source: "/auth/login",
        destination: `${backend}/auth/login`,
      },
      {
        source: "/auth/signout",
        destination: `${backend}/auth/signout`,
      },
      {
        source: "/auth/_dev/login",
        destination: `${backend}/auth/_dev/login`,
      },
      {
        source: "/v1/:path*",
        destination: `${backend}/v1/:path*`,
      },
      {
        source: "/health",
        destination: `${backend}/health`,
      },
    ];
  },
};

module.exports = nextConfig;
