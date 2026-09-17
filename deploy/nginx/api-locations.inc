# Shared by both vhosts. Kept out of conf.d/ so nginx's own `include
# /etc/nginx/conf.d/*.conf` does not pick it up a second time.

# The API, matched by path. Anything not listed here belongs to the app on that
# vhost, which is why the website can own `/` while the API owns `/v1`.

location /v1/ {
    proxy_pass http://chmabapay_api;
}

# The hosted checkout page a customer's browser is sent to.
location /pay/ {
    proxy_pass http://chmabapay_api;
}

# Sign-in, sign-out, and the Google callback the OAuth redirect URI points at.
location /auth/ {
    proxy_pass http://chmabapay_api;
}

location /user/google/ {
    proxy_pass http://chmabapay_api;
}

location = /openapi.json {
    proxy_pass http://chmabapay_api;
}

location = /health {
    proxy_pass http://chmabapay_api;
}

# --------------------------------------------------------------------------- #
# The dev rail, refused explicitly
# --------------------------------------------------------------------------- #
#
# `/auth/_dev/login` mints a real `chmabapay_session` for whatever `email` is
# named, with no credential. It lives under the `auth` router, so it is reached at
# `/auth/_dev/login` — under the `/auth/` prefix above. An earlier version of this
# file refused only `/_dev/*` and relied on there being "no location for it", which
# meant the one dev route that hands out sessions was proxied to the application
# and answered 307 with a valid cookie. Absence of a `location` is not a refusal;
# `return 404` is.
#
# `/auth/_dev/` is placed after `/auth/` deliberately. nginx picks the longest
# matching prefix, so `/auth/_dev/login` resolves here rather than to the API.
#
# 404 rather than 403: a refusal that does not confirm the path exists.
location = /_dev {
    return 404;
}

location /_dev/ {
    return 404;
}

location = /auth/_dev {
    return 404;
}

location /auth/_dev/ {
    return 404;
}

# --------------------------------------------------------------------------- #
# Deliberately NOT routed
# --------------------------------------------------------------------------- #
#
# `/metrics` — Prometheus exposition for the whole platform: request volumes by
# route, payment outcomes, queue depths. Scraping is an internal concern and
# Prometheus reaches `api:8000` directly on the compose network. Publishing it at
# the edge would disclose platform-wide volumes to anyone who asked, which is the
# thing the API warns about at startup when METRICS_TOKEN is unset.
#
# FastAPI's own `/docs` and `/redoc` are also unexposed: the public API reference
# is the website's `/api/docs` page. Add one location each if that changes.
