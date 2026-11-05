# Isolated provider-capture shim

This directory is placed first on `PYTHONPATH` only for the isolated
experiment service. Python imports `sitecustomize` at interpreter startup and
patches HTTPX requests sent to the loopback recording proxy.

The shim adds request-context IDs only. It does not read, record or log
Authorization, Cookie, API keys or response bodies.

Required environment for the isolated service process:

```text
CITRUS_PROVIDER_CAPTURE_PROXY_ORIGIN=http://127.0.0.1:8013
PYTHONPATH=<experiment checkout>/experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/provider_capture_sitecustomize:<normal PYTHONPATH>
```

The production service must not be started with this shim.
