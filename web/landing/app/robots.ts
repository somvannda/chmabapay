import type { MetadataRoute } from "next";
import { resolveSiteUrl } from "@/lib/siteUrl";

// The origin comes from the request, so this has to be rendered per request.
export const dynamic = "force-dynamic";

export default function robots(): MetadataRoute.Robots {
  const baseUrl = resolveSiteUrl();

  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Authenticated app, checkout hand-off and the API itself. Crawling any of
        // these either leaks customer data or burns rate-limit budget.
        disallow: ["/dashboard", "/onboarding", "/pay", "/v1", "/auth"],
      },
    ],
    sitemap: `${baseUrl}/sitemap.xml`,
    host: baseUrl,
  };
}
