import type { MetadataRoute } from "next";
import { resolveSiteUrl } from "@/lib/siteUrl";

// The origin comes from the request, so this has to be rendered per request.
export const dynamic = "force-dynamic";

/**
 * Only the public marketing pages. The dashboard, onboarding, checkout and API
 * routes are all behind auth or explicitly disallowed in robots.ts.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  const baseUrl = resolveSiteUrl();

  return [
    { url: `${baseUrl}/`, changeFrequency: "weekly", priority: 1 },
    { url: `${baseUrl}/api/docs`, changeFrequency: "monthly", priority: 0.8 },
    { url: `${baseUrl}/contact`, changeFrequency: "yearly", priority: 0.5 },
    { url: `${baseUrl}/privacy`, changeFrequency: "yearly", priority: 0.3 },
    { url: `${baseUrl}/terms`, changeFrequency: "yearly", priority: 0.3 },
  ];
}
