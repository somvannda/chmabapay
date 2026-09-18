# Prometheus in agent mode: scrape this deployment, forward it to Grafana Cloud.
#
# This is a TEMPLATE, not a config. Prometheus cannot read the environment from its
# own configuration file — there is no expansion available for `remote_write.url`,
# and `basic_auth` takes literals — so the compose entrypoint renders this to
# /tmp/prometheus.yml with the values from deploy/.env, then runs
# `promtool check config` on the result *before* starting Prometheus. A bad render
# therefore fails at boot rather than producing an instance that runs happily and
# silently forwards nothing.
#
# `__PLACEHOLDERS__` rather than `$VAR` so nothing in here can be mistaken for a
# shell expansion, and every substituted value is quoted so a token containing a
# YAML-significant character cannot break the parse.
#
# Agent mode (`--agent`, set in the compose service) keeps only discovery, scraping
# and remote_write: no local TSDB, no query API, no alerting. Note the flag name:
# `--enable-feature=agent` is the pre-3.x spelling and is silently *ignored* by 3.x
# (it logs `Unknown option for --enable-feature`), after which `--storage.agent.path`
# is rejected as agent-mode-only and the process exits. That is a real trap, and it
# is why the compose command prints its rendered config and runs `promtool` before
# starting rather than trusting the flags to be right.
# That is what lets this fit on a host with one core and a live POS stack beside it,
# and it is why Grafana Cloud is not a convenience here but the only place the data
# exists. Nothing in this file is load-bearing for being *told* about a problem —
# paging lives in the application (P0-5, P1-3) and goes out over Telegram whether
# this container is running or not.

global:
  # 60s rather than 15s. Grafana Cloud's free tier stores one data point per minute,
  # so scraping faster would be discarded or billed without adding resolution.
  # Nothing here is a paging signal, so a coarser scrape costs nothing that matters.
  scrape_interval: 60s
  scrape_timeout: 10s
  external_labels:
    # Stamped on every series in Grafana. Worth setting even for a single instance:
    # it is what lets a second environment be added later without the two silently
    # merging into one unattributable series.
    deployment: chmabapay-prod

scrape_configs:
  # The application. Token-gated in production, and deliberately not published at the
  # edge — this scrape runs across the compose network, so /metrics stays private and
  # no edge rule, certificate or firewall change is required.
  - job_name: chmabapay-api
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: "__METRICS_TOKEN__"
    static_configs:
      - targets: ["api:8000"]

  # The agent itself. Not decoration, and not redundant with the scrape above: in
  # agent mode there is no local query API, so if remote_write starts failing, these
  # `prometheus_remote_storage_*` series are the only thing that can report it. They
  # are also what separates "the API stopped publishing" from "the agent stopped
  # forwarding" — otherwise both are the same observation: silence.
  - job_name: prometheus
    static_configs:
      - targets: ["localhost:9090"]

# Delivery. The default queue_config is left alone deliberately: it is tuned for far
# more series than this deployment produces, and the remote end's ingest limits are
# the binding constraint long before Prometheus's own buffering is.
remote_write:
  - url: "__GRAFANA_REMOTE_WRITE_URL__"
    basic_auth:
      username: "__GRAFANA_METRICS_USERNAME__"
      password: "__GRAFANA_METRICS_TOKEN__"
