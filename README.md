# vLLM + SGLang Observability

A small, self-hosted dashboard for vLLM and SGLang Prometheus metrics.

## Run

```bash
pip install -r requirements.txt
python run.py
```

Open http://localhost:7860 and enter the vLLM or SGLang server root URL, for
example `http://localhost:8000`. The dashboard polls `{url}/metrics` every
one second by default.

For SGLang, start the server with `--enable-metrics`; the dashboard detects
the `sglang:` metric family automatically and exposes its KV usage, Radix cache
hit rate, request queue, token throughput, speculative decoding, TTFT, E2E,
and TPOT metrics.

The speculative-decoding panels deliberately differ by runtime. vLLM exports
draft, accepted-token, and draft-round counters, so the dashboard derives live
acceptance rate, accepted length, and draft/accepted token rates. SGLang's
Prometheus metrics expose configured speculative steps and draft tokens; those
are shown as configuration signals rather than inventing an acceptance-rate
statistic that the endpoint did not export.

The default sampling interval is one second. Set `METRICS_POLL_INTERVAL` to
any value from 1 to 60 seconds when a different collection cadence is needed.
The cumulative cards aggregate counter series by runtime, engine type, and
model name, summing rank/source/reason label dimensions inside each group.
Groups that disappear are retained for five minutes by default; set
`CUMULATIVE_METRICS_TTL_SECONDS` (30-86400) to change the cleanup TTL.

For unattended deployment, set `VLLM_METRICS_URL` and optionally
`VLLM_METRICS_API_KEY` before starting the service.

The dashboard includes metric discovery, time-series history, latency
histograms, alert thresholds, exports, and a local demo mode.

KV cache observability includes hit counters and hit rate for both runtimes.
For vLLM, the dashboard derives the interval hit rate from
`prefix_cache_hits / prefix_cache_queries`. For SGLang, it derives a token-weighted
interval rate from `cached_tokens / prompt_tokens` instead of averaging the
last-batch `cache_hit_rate` gauge. Cache hit rate and SGLang eviction deltas are
shown as time-series charts rather than cumulative summary cards. vLLM does not
expose a true eviction counter, so no vLLM eviction series is synthesized. Its
sampled block-eviction idle time, lifetime, and reuse gaps appear when started with
`--kv-cache-metrics-sample` set to a non-zero sampling rate; request
preemptions remain a separate metric because they are not cache evictions.
