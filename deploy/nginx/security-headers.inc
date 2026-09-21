# Security headers sent with every response on both vhosts.
#
# This is a separate file, and mounted separately, for one nginx reason worth
# knowing before you move these lines: `add_header` is inherited from the enclosing
# level **only when the inner block defines none of its own**. So a location that
# sets a header of its own — the `Cache-Control` overrides in nginx.prod.conf and
# nginx.conf — does not inherit anything from its server block, and has to
# re-include this file. If you add an `add_header` anywhere, include this file at
# the same level or the security headers silently vanish from that response.
#
# Why each one:
#
#   X-Frame-Options: DENY  and  Content-Security-Policy: frame-ancestors 'none'
#       The pair that matters. The merchant dashboard and the platform console are
#       session-authenticated and were framable, which is a clickjacking surface on
#       precisely the two screens that can move money and rotate credentials. DENY
#       covers older browsers; the CSP directive covers modern ones.
#
#       The CSP is deliberately one directive and no more. A `script-src` or
#       `style-src` here would have to account for Next.js's inline bootstrap and
#       its pre-hydration script, and getting that wrong breaks both applications
#       rather than tightening them. `frame-ancestors` cannot break anything: it
#       governs embedding and nothing else.
#
#   X-Content-Type-Options: nosniff
#       Stops a browser from second-guessing a `Content-Type` — the classic route
#       from an uploaded or user-influenced response to script execution.
#
#   Referrer-Policy
#       Keeps the full URL of a dashboard or checkout page out of the `Referer`
#       sent to whatever a merchant or payer clicks next. `strict-origin-when-
#       cross-origin` still sends the origin to third parties, which is what makes
#       it usable, and never the path.
#
#   Permissions-Policy
#       Nothing here uses these APIs, so they are switched off rather than left to
#       whatever default a future browser ships. `payment` is included because the
#       Payment Request API would otherwise be reachable from a page that has a
#       `checkout_url` in it, and this platform deliberately has no card path.

add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Content-Security-Policy "frame-ancestors 'none'" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;
