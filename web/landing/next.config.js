/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  experimental: {
    typedRoutes: false,
  },
  // Evaluated by the router *before* any page renders, so an old link resolves in
  // a single HTTP hop instead of downloading a shell and then navigating again.
  //
  // P2-1: payment detail used to exist at two URLs, maintained independently, and
  // they had already drifted — the dev "Test: mark paid" action existed on only
  // one of them. `/dashboard/payments/{id}` is now the single implementation and
  // the store-scoped URL forwards here.
  //
  // `permanent: false` (307) rather than a 308 on purpose: a 308 is cached hard by
  // browsers, and if this route ever comes back, undoing it in the field is
  // painful. A redirect that costs one extra hop is cheaper than a redirect that
  // cannot be withdrawn.
  async redirects() {
    return [
      {
        source: "/dashboard/:public_id/payments/:pay_id",
        destination: "/dashboard/payments/:pay_id",
        permanent: false,
      },
      // Webhooks and API keys are workspace-scoped, so the store-scoped URLs stopped
      // being pages. They used to answer with a page that redirected itself from an
      // effect, which meant the browser painted the store's chrome and a "Redirecting…"
      // notice first; here the redirect is decided by the router, before any render.
      // Same 307 for the same reason as the payment route above.
      {
        source: "/dashboard/:public_id/webhooks",
        destination: "/dashboard/webhooks",
        permanent: false,
      },
      {
        source: "/dashboard/:public_id/api-keys",
        destination: "/dashboard/keys",
        permanent: false,
      },
      // Two conventional entry points people type or guess. Neither has a page
      // of its own: sign-in is Google OAuth, and pricing is the `#plans`
      // section of the landing page. Without this they are bare 404s, which is
      // what the production audit found.
      {
        source: "/login",
        destination: "/auth/google/login",
        permanent: false,
      },
      {
        source: "/pricing",
        destination: "/#plans",
        permanent: false,
      },
    ];
  },
  async rewrites() {
    const backend =
      process.env.NEXT_PUBLIC_API_URL ??
      process.env.BACKEND_URL ??
      "http://127.0.0.1:8000";
    return [
      {
        source: "/user/google/auth/login",
        destination: `${backend}/user/google/auth/login`,
      },
      {
        source: "/user/google/auth/callback",
        destination: `${backend}/user/google/auth/callback`,
      },
      {
        source: "/auth/google/login",
        destination: `${backend}/user/google/auth/login`,
      },
      {
        source: "/auth/google/callback",
        destination: `${backend}/user/google/auth/callback`,
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
        source: "/_dev/:path*",
        destination: `${backend}/_dev/:path*`,
      },
      {
        source: "/pay/:path*",
        destination: `${backend}/pay/:path*`,
      },
      {
        source: "/api/v1/:path*",
        destination: `${backend}/api/v1/:path*`,
      },
      {
        source: "/openapi.json",
        destination: `${backend}/openapi.json`,
      },
      {
        source: "/health",
        destination: `${backend}/health`,
      },
    ];
  },
};

module.exports = nextConfig;
