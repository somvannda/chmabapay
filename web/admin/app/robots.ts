import type { MetadataRoute } from "next";

/**
 * The console sits on a public hostname but is an internal tool, so the answer to
 * "may I index this" is a blanket no.
 *
 * This is the second of two layers, and neither is redundant: the `robots` field in
 * `layout.tsx` is a meta tag, which a crawler only sees after it has already fetched
 * and rendered the page. This file refuses the fetch.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", disallow: "/" }],
  };
}
