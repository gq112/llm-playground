"""A focused, self-hosted observability dashboard for vLLM and SGLang metrics."""

import asyncio
import json
import logging
import math
import os
import random
import re
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .dcgm import DcgmStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
TARGET_PATH = Path.home() / ".vllm-observability" / "target.json"
DCGM_TARGET_PATH = Path.home() / ".vllm-observability" / "dcgm-target.json"

app = FastAPI(title="推理指标观测仪表盘", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


class ObservabilityTargetRequest(BaseModel):
    """The vLLM or SGLang HTTP root; metrics are fetched from ``{url}/metrics``."""

    url: str = Field(min_length=1, max_length=2048)
    api_key: Optional[str] = Field(default=None, max_length=4096)


class DcgmTargetRequest(BaseModel):
    """The dcgm-exporter HTTP root; metrics are fetched from ``{url}/metrics``."""

    url: str = Field(min_length=1, max_length=2048)


class SimulateMetricsRequest(BaseModel):
    kv_cache_usage_perc: Optional[float] = None
    prefix_cache_hit_rate: Optional[float] = None
    num_preemptions: Optional[float] = None
    num_requests_running: Optional[float] = None
    num_requests_waiting: Optional[float] = None
    prefix_cache_hits: Optional[float] = None
    prefix_cache_queries: Optional[float] = None
    kv_evictions: Optional[float] = None
    gpu_cache_usage_perc: Optional[float] = None
    cpu_cache_usage_perc: Optional[float] = None
    spec_decode_accepted: Optional[float] = None
    spec_decode_draft: Optional[float] = None


class MetricStore:
    """Poll a vLLM or SGLang Prometheus endpoint and retain local history."""

    _CUMULATIVE_FIELD_SUFFIXES = {
        "requests": ("num_requests", "requests", "request_success"),
        "input_tokens": ("prompt_tokens",),
        "output_tokens": ("generation_tokens",),
        "kv_hits": ("cached_tokens", "prefix_cache_hits"),
        "kv_queries": ("prefix_cache_queries",),
        "kv_evictions": ("evicted_tokens",),
        "spec_accepted": ("spec_decode_num_accepted_tokens",),
        "spec_drafts": ("spec_decode_num_draft_tokens",),
        "spec_rounds": ("spec_decode_num_drafts",),
    }
    _ADDITIVE_GAUGE_SUFFIXES = {
        "num_requests_running",
        "num_requests_waiting",
        "num_running_reqs",
        "num_queue_reqs",
        "num_used_tokens",
    }
    _MAX_GAUGE_SUFFIXES = {
        "kv_cache_usage_perc",
        "gpu_cache_usage_perc",
        "cpu_cache_usage_perc",
        "token_usage",
    }
    _LABEL_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:\\.|[^"\\])*)"')

    def __init__(
        self,
        interval: float = 2.0,
        history_size: int = 8640,
        history_retention_seconds: float = 172800.0,
        archive_interval_seconds: float = 30.0,
        cumulative_ttl_seconds: float = 300.0,
    ):
        self.interval = max(1.0, min(interval, 60.0))
        self.history_retention_seconds = max(300.0, history_retention_seconds)
        self.archive_interval_seconds = max(self.interval, archive_interval_seconds)
        self.cumulative_ttl_seconds = max(1.0, cumulative_ttl_seconds)
        self.target_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.cumulative_groups: Dict[tuple, Dict[str, Any]] = {}
        self._latest_group_intervals: Dict[tuple, Dict[str, Any]] = {}
        self._histogram_series_state: Dict[tuple, float] = {}
        self._latest_histogram_intervals: Dict[str, float] = {}
        self.history: deque = deque(maxlen=history_size)
        archive_size = math.ceil(self.history_retention_seconds / self.archive_interval_seconds) + 1
        self.history_archive: deque = deque(maxlen=archive_size)
        self._last_archive_timestamp: Optional[datetime] = None
        self.last_scrape: Optional[datetime] = None
        self.last_simulated: Optional[datetime] = None
        self._task: Optional[asyncio.Task] = None
        self._warned = False

    def configure(self, url: str, api_key: Optional[str]) -> str:
        normalized = url.strip().rstrip("/")
        if normalized.lower().endswith("/v1"):
            normalized = normalized[:-3].rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Metrics source must be an absolute http(s) URL")
        self.target_url = normalized
        self.api_key = api_key or None
        self.metrics.clear()
        self.history.clear()
        self.history_archive.clear()
        self._last_archive_timestamp = None
        self.cumulative_groups.clear()
        self._latest_group_intervals.clear()
        self._histogram_series_state.clear()
        self._latest_histogram_intervals.clear()
        self.last_scrape = None
        self.last_simulated = None
        self._warned = False
        return normalized

    def load_target(self) -> None:
        url = os.getenv("VLLM_METRICS_URL", "").strip()
        api_key = os.getenv("VLLM_METRICS_API_KEY", "") or None
        if not url and TARGET_PATH.exists():
            try:
                saved = json.loads(TARGET_PATH.read_text(encoding="utf-8"))
                url = str(saved.get("url") or "").strip()
                api_key = saved.get("api_key") or None
            except (OSError, ValueError, TypeError):
                logger.warning("Ignoring unreadable saved metrics target")
        if url:
            try:
                self.configure(url, api_key)
            except ValueError as exc:
                logger.warning("Ignoring invalid metrics target: %s", exc)

    def save_target(self) -> None:
        if not self.target_url:
            return
        try:
            TARGET_PATH.parent.mkdir(parents=True, exist_ok=True)
            TARGET_PATH.write_text(
                json.dumps({"url": self.target_url, "api_key": self.api_key}), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Could not save metrics target: %s", exc)

    async def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self) -> None:
        while True:
            await self.scrape()
            await asyncio.sleep(self.interval)

    def _mark_unavailable(self) -> None:
        """Invalidate current values after a failed remote scrape.

        History is intentionally retained for later inspection, but it must
        never be presented as the current state of an unavailable service.
        """
        self.metrics.clear()
        self.last_scrape = None
        self.last_simulated = None

    async def scrape(self) -> bool:
        if not self.target_url:
            return False
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(f"{self.target_url}/metrics") as response:
                    if response.status != 200:
                        raise RuntimeError(f"/metrics returned HTTP {response.status}")
                    parsed, samples = self._parse_prometheus_payload(await response.text())
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            if not self._warned:
                logger.warning("Cannot scrape vLLM metrics from %s: %s", self.target_url, exc)
                self._warned = True
            self._mark_unavailable()
            return False

        if not parsed:
            self._mark_unavailable()
            return False
        now = datetime.now()
        self.metrics = parsed
        self._update_cumulative_groups(samples, now)
        self._update_histogram_intervals(samples)
        self.last_scrape = now
        self.last_simulated = None
        self._warned = False
        self._append_snapshot()
        return True

    @staticmethod
    def parse_prometheus(text: str) -> Dict[str, Dict[str, Any]]:
        return MetricStore._parse_prometheus_payload(text)[0]

    @staticmethod
    def _parse_prometheus_payload(text: str) -> tuple:
        types: Dict[str, str] = {}
        metrics: Dict[str, Dict[str, Any]] = {}
        buckets: Dict[str, Dict[float, float]] = {}
        metric_series: Dict[str, list] = {}
        samples = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("# TYPE "):
                parts = line.split()
                if len(parts) >= 4:
                    types[parts[2]] = parts[3]
                    types[MetricStore._base_name(parts[2])] = parts[3]
                continue
            if line.startswith("#"):
                continue
            sample = MetricStore._parse_sample(line)
            if not sample:
                continue
            name, labels, value = sample
            if not math.isfinite(value):
                continue
            if not name.startswith(("vllm:", "sglang:")):
                continue
            base = MetricStore._base_name(name)
            if name.endswith("_bucket"):
                le = MetricStore._bucket_limit(labels)
                if le is not None:
                    histogram_buckets = buckets.setdefault(base, {})
                    histogram_buckets[le] = histogram_buckets.get(le, 0.0) + value
                    parsed_labels = MetricStore._parse_labels(labels)
                    parsed_labels.pop("le", None)
                    label = "+Inf" if le == float("inf") else str(le)
                    samples.append({
                        "key": f"{base}_bucket_le_{label}",
                        "value": value,
                        "labels": parsed_labels,
                        "histogram_component": True,
                    })
                continue
            if name.endswith("_sum") or name.endswith("_count"):
                key = f"{base}{'_sum' if name.endswith('_sum') else '_count'}"
                metric_series.setdefault(key, []).append({"value": value, "labels": labels})
                samples.append({
                    "key": key,
                    "value": value,
                    "labels": MetricStore._parse_labels(labels),
                    "histogram_component": types.get(base) == "histogram",
                })
                continue
            key = base if name.endswith("_total") else name
            metric_series.setdefault(key, []).append({"value": value, "labels": labels})
            samples.append({
                "key": key,
                "value": value,
                "labels": MetricStore._parse_labels(labels),
            })
        for key, series in metric_series.items():
            base = MetricStore._base_name(key)
            metric_type = "counter" if key.endswith(("_sum", "_count")) else types.get(base, "gauge")
            metrics[key] = MetricStore._aggregate_metric_series(key, metric_type, series)
        for base, values in buckets.items():
            bucket_values = list(values.items())
            percentiles = MetricStore._percentiles(bucket_values)
            if percentiles:
                metrics[base] = {"type": "histogram", "labels": "", **percentiles}
                for limit, count in bucket_values:
                    label = "+Inf" if limit == float("inf") else str(limit)
                    metrics[f"{base}_bucket_le_{label}"] = {
                        "value": count, "type": "histogram_bucket", "labels": f'le="{label}"'
                    }
        return metrics, samples

    @staticmethod
    def _aggregate_metric_series(key: str, metric_type: str, series: list) -> Dict[str, Any]:
        """Keep every labeled series and expose an explicit flat aggregate.

        Counters and count-like gauges are additive. Capacity ratios use the
        busiest engine/rank for operational safety. Other multi-series gauges
        use a documented arithmetic mean instead of silently keeping whichever
        Prometheus line happened to be parsed last.
        """
        values = [item["value"] for item in series]
        suffix = key.split(":", 1)[-1]
        if metric_type == "counter" or suffix in MetricStore._ADDITIVE_GAUGE_SUFFIXES:
            value, aggregation = sum(values), "sum"
        elif suffix in MetricStore._MAX_GAUGE_SUFFIXES:
            value, aggregation = max(values), "max"
        elif len(values) == 1:
            value, aggregation = values[0], "identity"
        else:
            value, aggregation = sum(values) / len(values), "mean"
        output: Dict[str, Any] = {
            "value": value,
            "type": metric_type,
            "labels": series[0]["labels"] if len(series) == 1 else "",
            "aggregation": aggregation,
            "series_count": len(series),
        }
        if len(series) > 1:
            output["series"] = series
        return output

    @staticmethod
    def _parse_labels(labels: str) -> Dict[str, str]:
        parsed = {}
        for match in MetricStore._LABEL_PATTERN.finditer(labels):
            value = match.group(2).replace(r'\"', '"').replace(r"\\", "\\")
            parsed[match.group(1)] = value
        return parsed

    def _update_cumulative_groups(self, samples: list, now: Optional[datetime] = None) -> None:
        """Aggregate counters by runtime, engine type, and model.

        Every full Prometheus label series tracks its own raw counter and reset
        state. Only the resulting totals and interval deltas are aggregated by
        model and engine, so one restarted rank cannot inflate the other ranks.
        """
        observed_at = now or datetime.now()
        grouped: Dict[tuple, Dict[str, Any]] = {}
        self._latest_group_intervals = {}
        candidate_keys = {
            f"{runtime}:{suffix}"
            for runtime in ("vllm", "sglang")
            for suffixes in self._CUMULATIVE_FIELD_SUFFIXES.values()
            for suffix in suffixes
        }
        gauge_keys = {"sglang:cache_hit_rate", "vllm:prefix_cache_hit_rate"}
        for sample in samples:
            key = sample.get("key", "")
            value = sample.get("value")
            if (
                key not in candidate_keys | gauge_keys
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                continue
            runtime = key.split(":", 1)[0]
            labels = sample.get("labels") or {}
            engine_type = labels.get("engine_type") or runtime
            model_name = labels.get("model_name") or labels.get("served_model_name") or "unknown"
            group_key = (runtime, engine_type, model_name)
            group_values = grouped.setdefault(group_key, {"counters": {}, "gauges": {}})
            if key in gauge_keys:
                gauge = group_values["gauges"].setdefault(key, {"sum": 0.0, "count": 0})
                gauge["sum"] += value
                gauge["count"] += 1
            else:
                counters = group_values["counters"]
                series_key = tuple(sorted(labels.items()))
                raw_series = counters.setdefault(key, {})
                raw_series[series_key] = raw_series.get(series_key, 0.0) + value

        for (runtime, engine_type, model_name), observed in grouped.items():
            group_key = (runtime, engine_type, model_name)
            group = self.cumulative_groups.get(group_key)
            previous_seen = group["last_seen"] if group else None
            if group is None:
                group = {
                    "runtime": runtime,
                    "engine_type": engine_type,
                    "model_name": model_name,
                    "last_seen": observed_at,
                    "counters": {},
                }
                self.cumulative_groups[group_key] = group
            elapsed = (observed_at - previous_seen).total_seconds() if previous_seen else None
            group["last_seen"] = observed_at
            counters = group["counters"]
            deltas = {}
            raw_metrics = observed["counters"]
            for field, suffixes in self._CUMULATIVE_FIELD_SUFFIXES.items():
                candidates = [f"{runtime}:{suffix}" for suffix in suffixes]
                source = next((candidate for candidate in candidates if candidate in raw_metrics), None)
                if source is None:
                    continue
                raw_series = raw_metrics[source]
                state = counters.get(field)
                if state is None:
                    counters[field] = {
                        "source": source,
                        "series": dict(raw_series),
                        "total": sum(raw_series.values()),
                    }
                    continue
                if state["source"] != source:
                    state.update({"source": source, "series": dict(raw_series)})
                    continue
                interval_delta = 0.0
                has_baseline = False
                for series_key, raw_value in raw_series.items():
                    previous_raw = state["series"].get(series_key)
                    if previous_raw is None:
                        state["series"][series_key] = raw_value
                        state["total"] += raw_value
                        continue
                    increment = raw_value - previous_raw if raw_value >= previous_raw else raw_value
                    state["series"][series_key] = raw_value
                    state["total"] += increment
                    interval_delta += increment
                    has_baseline = True
                if has_baseline:
                    deltas[field] = interval_delta

            group["cache_hit_rate"] = None
            denominator_field = "kv_queries" if runtime == "vllm" else "input_tokens"
            denominator = deltas.get(denominator_field)
            if denominator is not None and denominator > 0 and "kv_hits" in deltas:
                group["cache_hit_rate"] = deltas["kv_hits"] / denominator
            elif runtime == "vllm":
                hit_rate_gauge = observed["gauges"].get("vllm:prefix_cache_hit_rate")
                if hit_rate_gauge and hit_rate_gauge["count"]:
                    group["cache_hit_rate"] = hit_rate_gauge["sum"] / hit_rate_gauge["count"]

            group["kv_evictions_per_sample"] = None
            if "kv_evictions" in deltas:
                group["kv_evictions_per_sample"] = deltas["kv_evictions"]
            if runtime == "sglang":
                group["kv_eviction_unit"] = "tokens"
            if elapsed is not None and elapsed > 0:
                self._latest_group_intervals[group_key] = {"elapsed": elapsed, "deltas": deltas}

        self._cleanup_cumulative_groups(observed_at)

    def _update_histogram_intervals(self, samples: list) -> None:
        """Calculate histogram component deltas per complete label series."""
        observed: Dict[str, Dict[tuple, float]] = {}
        for sample in samples:
            if not sample.get("histogram_component"):
                continue
            key = sample["key"]
            series_key = tuple(sorted((sample.get("labels") or {}).items()))
            series = observed.setdefault(key, {})
            series[series_key] = series.get(series_key, 0.0) + sample["value"]

        intervals: Dict[str, float] = {}
        for key, series in observed.items():
            for series_key, raw_value in series.items():
                state_key = (key, series_key)
                previous_raw = self._histogram_series_state.get(state_key)
                self._histogram_series_state[state_key] = raw_value
                if previous_raw is None:
                    continue
                increment = raw_value - previous_raw if raw_value >= previous_raw else raw_value
                intervals[key] = intervals.get(key, 0.0) + increment
        self._latest_histogram_intervals = intervals

    def _cleanup_cumulative_groups(self, now: Optional[datetime] = None) -> None:
        observed_at = now or datetime.now()
        cutoff = observed_at - timedelta(seconds=self.cumulative_ttl_seconds)
        for key, group in list(self.cumulative_groups.items()):
            if group["last_seen"] < cutoff:
                self.cumulative_groups.pop(key, None)

    def get_cumulative_groups(self, now: Optional[datetime] = None) -> list:
        observed_at = now or datetime.now()
        self._cleanup_cumulative_groups(observed_at)
        output = []
        for group in self.cumulative_groups.values():
            age = max(0.0, (observed_at - group["last_seen"]).total_seconds())
            values = {
                field: state["total"]
                for field, state in group["counters"].items()
                if field in {"requests", "input_tokens", "output_tokens"}
            }
            if not values:
                continue
            output.append({
                "runtime": group["runtime"],
                "engine_type": group["engine_type"],
                "model_name": group["model_name"],
                "updated_at": group["last_seen"].isoformat(),
                "age_seconds": round(age, 1),
                "expires_in_seconds": round(max(0.0, self.cumulative_ttl_seconds - age), 1),
                "values": values,
            })
        return sorted(output, key=lambda item: (item["model_name"], item["runtime"], item["engine_type"]))

    @staticmethod
    def _parse_sample(line: str):
        brace = line.find("{")
        if brace >= 0:
            close = line.find("}", brace)
            if close < 0:
                return None
            name, labels, rest = line[:brace], line[brace + 1:close], line[close + 1:].strip()
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                return None
            name, labels, rest = parts[0], "", parts[1]
        try:
            return name, labels, float(rest.split()[0])
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _base_name(name: str) -> str:
        for suffix in ("_total", "_bucket", "_count", "_sum", "_created"):
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return name

    @staticmethod
    def _bucket_limit(labels: str) -> Optional[float]:
        for item in labels.split(","):
            if item.strip().startswith("le="):
                raw = item.split("=", 1)[1].strip().strip('"')
                if raw == "+Inf":
                    return float("inf")
                try:
                    return float(raw)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _percentiles(values: list) -> Dict[str, float]:
        """Estimate classic-histogram quantiles like Prometheus histogram_quantile.

        Bucket counts are cumulative. Invalid or slightly inconsistent input is
        repaired to monotonic counts before linear interpolation, matching the
        behavior that makes PromQL robust to floating-point and scrape noise.
        """
        values = sorted(values, key=lambda item: item[0])
        if (
            len(values) < 2
            or values[-1][0] != float("inf")
            or any(not math.isfinite(count) or count < 0 for _, count in values)
        ):
            return {}

        monotonic = []
        previous_count = 0.0
        for limit, count in values:
            corrected_count = max(previous_count, count)
            monotonic.append((limit, corrected_count))
            previous_count = corrected_count

        total = monotonic[-1][1]
        if total <= 0:
            return {}

        output = {}
        for name, ratio in (("p50", .5), ("p90", .9), ("p95", .95), ("p99", .99)):
            threshold = total * ratio
            previous_limit, previous_count = 0.0, 0.0
            for index, (limit, count) in enumerate(monotonic):
                if limit == float("inf"):
                    output[name] = previous_limit
                    break
                if count >= threshold:
                    if index == 0 and limit <= 0:
                        output[name] = limit
                        break
                    bucket_count = count - previous_count
                    fraction = 0.0 if bucket_count <= 0 else (threshold - previous_count) / bucket_count
                    output[name] = previous_limit + fraction * (limit - previous_limit)
                    break
                previous_limit, previous_count = limit, count
        return output

    def _append_snapshot(self, timestamp: Optional[datetime] = None, values: Optional[Dict[str, float]] = None) -> None:
        observed_at = timestamp or datetime.now()
        snapshot = {"timestamp": observed_at.isoformat()}
        source = values or self.metrics
        for key, entry in source.items():
            if not isinstance(entry, dict):
                snapshot[key] = entry
                continue
            if entry.get("type") in ("histogram", "derived"):
                continue
            snapshot[key] = entry.get("value", entry.get("p50"))
        derived = self._derive_rates(snapshot)
        snapshot.update({
            key: value for key, value in derived.items()
            if not key.endswith("::interval")
        })
        self.history.append(snapshot)
        self._archive_snapshot(snapshot, observed_at)
        if values is None:
            for key, entry in list(self.metrics.items()):
                if not isinstance(entry, dict):
                    continue
                if entry.get("type") == "histogram":
                    for statistic in ("p50", "p90", "p95", "p99", "avg"):
                        entry.pop(statistic, None)
                elif entry.get("type") == "histogram_bucket":
                    entry["interval_value"] = None
                elif entry.get("type") == "derived":
                    self.metrics.pop(key, None)
        if derived and values is None:
            histogram_statistics: Dict[str, Dict[str, float]] = {}
            for key, value in derived.items():
                if "::" not in key:
                    continue
                base, statistic = key.rsplit("::", 1)
                if statistic == "interval":
                    entry = self.metrics.get(base)
                    if entry and entry.get("type") == "histogram_bucket":
                        entry["interval_value"] = value
                    continue
                entry = self.metrics.get(base)
                if entry and entry.get("type") == "histogram":
                    histogram_statistics.setdefault(base, {})[statistic] = value
            for base, statistics in histogram_statistics.items():
                self.metrics[base].update(statistics)
            self.metrics.update({
                key: {"value": value, "type": "derived", "labels": ""}
                for key, value in derived.items()
                if "::" not in key
            })

    def _archive_snapshot(self, snapshot: Dict[str, Any], observed_at: datetime) -> None:
        """Keep a sparse 48-hour history without retaining every raw scrape."""
        if (
            self._last_archive_timestamp is not None
            and observed_at >= self._last_archive_timestamp
            and (observed_at - self._last_archive_timestamp).total_seconds() < self.archive_interval_seconds
        ):
            return
        if self._last_archive_timestamp is not None and observed_at < self._last_archive_timestamp:
            self.history_archive.clear()
        self.history_archive.append(snapshot.copy())
        self._last_archive_timestamp = observed_at
        cutoff = observed_at - timedelta(seconds=self.history_retention_seconds)
        while (
            self.history_archive
            and datetime.fromisoformat(self.history_archive[0]["timestamp"]) < cutoff
        ):
            self.history_archive.popleft()

    def get_history(self, seconds: Optional[float] = None, now: Optional[datetime] = None) -> list:
        """Return dense recent samples merged with sparse long-term samples."""
        merged = {
            point["timestamp"]: point
            for point in self.history_archive
        }
        merged.update({point["timestamp"]: point for point in self.history})
        points = sorted(merged.values(), key=lambda point: point["timestamp"])
        if seconds is None:
            return points
        cutoff = (now or datetime.now()) - timedelta(seconds=max(seconds, 0))
        return [
            point for point in points
            if datetime.fromisoformat(point["timestamp"]) >= cutoff
        ]

    def _derive_rates(self, snapshot: Dict[str, Any]) -> Dict[str, float]:
        """Derive short-interval cache, token, and spec-decode metrics."""
        if not self.history:
            return {}
        previous = self.history[-1]
        try:
            elapsed = (
                datetime.fromisoformat(snapshot["timestamp"])
                - datetime.fromisoformat(previous["timestamp"])
            ).total_seconds()
        except (KeyError, ValueError):
            return {}
        if elapsed <= 0:
            return {}

        def delta(key: str) -> Optional[float]:
            current, before = snapshot.get(key), previous.get(key)
            if not isinstance(current, (int, float)) or not isinstance(before, (int, float)):
                return None
            change = current - before
            return change if change >= 0 else None

        def grouped_delta(runtime: str, field: str) -> Optional[float]:
            values = [
                interval["deltas"][field]
                for group_key, interval in self._latest_group_intervals.items()
                if group_key[0] == runtime and field in interval["deltas"]
            ]
            return sum(values) if values else None

        def grouped_rate(runtime: str, field: str) -> Optional[float]:
            rates = [
                interval["deltas"][field] / interval["elapsed"]
                for group_key, interval in self._latest_group_intervals.items()
                if group_key[0] == runtime
                and field in interval["deltas"]
                and interval["elapsed"] > 0
            ]
            return sum(rates) if rates else None

        use_grouped_deltas = bool(self._latest_group_intervals)

        output: Dict[str, float] = {}

        # Current vLLM releases export prefix-cache hits and queries as token
        # counters rather than a hit-rate gauge. Only emit a value for an
        # interval that actually processed queried tokens; idle intervals are
        # gaps instead of stale horizontal data.
        if "vllm:prefix_cache_hit_rate" not in snapshot:
            cache_hits = (
                grouped_delta("vllm", "kv_hits")
                if use_grouped_deltas
                else delta("vllm:prefix_cache_hits")
            )
            cache_queries = (
                grouped_delta("vllm", "kv_queries")
                if use_grouped_deltas
                else delta("vllm:prefix_cache_queries")
            )
            if cache_queries is not None and cache_queries > 0 and cache_hits is not None:
                output["observability:prefix_cache_hit_rate"] = cache_hits / cache_queries

        sglang_hits = (
            grouped_delta("sglang", "kv_hits")
            if use_grouped_deltas
            else delta("sglang:cached_tokens")
        )
        sglang_prompt = (
            grouped_delta("sglang", "input_tokens")
            if use_grouped_deltas
            else delta("sglang:prompt_tokens")
        )
        if sglang_prompt is not None and sglang_prompt > 0 and sglang_hits is not None:
            output["observability:sglang_cache_hit_rate"] = sglang_hits / sglang_prompt

        # Prometheus histogram buckets, sums, and counts are counters. Use
        # their deltas between adjacent scrapes so values describe this sample
        # interval rather than the server lifetime.
        buckets_by_histogram: Dict[str, list] = {}
        invalid_histograms = set()
        marker = "_bucket_le_"
        histogram_components = self._latest_histogram_intervals
        component_keys = histogram_components.keys() if histogram_components else snapshot.keys()
        for key in component_keys:
            if marker not in key:
                continue
            base, raw_limit = key.rsplit(marker, 1)
            if self.metrics.get(base, {}).get("type") != "histogram":
                continue
            count = histogram_components.get(key) if histogram_components else delta(key)
            if count is None:
                invalid_histograms.add(base)
                continue
            try:
                limit = float("inf") if raw_limit == "+Inf" else float(raw_limit)
            except ValueError:
                continue
            buckets_by_histogram.setdefault(base, []).append((key, limit, count))

        for base, buckets in buckets_by_histogram.items():
            if base in invalid_histograms:
                continue
            bucket_values = [(limit, count) for _, limit, count in buckets]
            bucket_values.sort(key=lambda item: item[0])
            if not bucket_values or bucket_values[-1][0] != float("inf"):
                continue
            total_requests = (
                histogram_components.get(f"{base}_count")
                if histogram_components
                else delta(f"{base}_count")
            )
            if (
                total_requests is None
                or total_requests < 0
                or not math.isclose(total_requests, bucket_values[-1][1], rel_tol=1e-9, abs_tol=1e-9)
            ):
                continue
            for bucket_key, _, count in buckets:
                output[f"{bucket_key}::interval"] = count
            for percentile, value in self._percentiles(bucket_values).items():
                output[f"{base}::{percentile}"] = value
            total_duration = (
                histogram_components.get(f"{base}_sum")
                if histogram_components
                else delta(f"{base}_sum")
            )
            if total_duration is not None and total_requests is not None and total_requests > 0:
                output[f"{base}::avg"] = total_duration / total_requests
        for prefix in ("vllm:", "sglang:"):
            runtime = prefix[:-1]
            prompt_rate = grouped_rate(runtime, "input_tokens") if use_grouped_deltas else None
            generated_rate = grouped_rate(runtime, "output_tokens") if use_grouped_deltas else None
            if not use_grouped_deltas:
                prompt = delta(f"{prefix}prompt_tokens")
                generated = delta(f"{prefix}generation_tokens")
                prompt_rate = prompt / elapsed if prompt is not None else None
                generated_rate = generated / elapsed if generated is not None else None
            if prompt_rate is not None:
                output["observability:prompt_token_rate"] = prompt_rate
            if generated_rate is not None:
                output["observability:generation_token_rate"] = generated_rate
            if prompt_rate is not None and generated_rate is not None:
                output["observability:total_token_rate"] = prompt_rate + generated_rate

        sglang_evictions = (
            grouped_delta("sglang", "kv_evictions")
            if use_grouped_deltas
            else delta("sglang:evicted_tokens")
        )
        if sglang_evictions is not None:
            output["observability:kv_evictions_per_sample"] = sglang_evictions

        accepted = (
            grouped_delta("vllm", "spec_accepted")
            if use_grouped_deltas
            else delta("vllm:spec_decode_num_accepted_tokens")
        )
        drafts = (
            grouped_delta("vllm", "spec_drafts")
            if use_grouped_deltas
            else delta("vllm:spec_decode_num_draft_tokens")
        )
        rounds = (
            grouped_delta("vllm", "spec_rounds")
            if use_grouped_deltas
            else delta("vllm:spec_decode_num_drafts")
        )
        accepted_rate = (
            grouped_rate("vllm", "spec_accepted")
            if use_grouped_deltas
            else (accepted / elapsed if accepted is not None else None)
        )
        draft_rate = (
            grouped_rate("vllm", "spec_drafts")
            if use_grouped_deltas
            else (drafts / elapsed if drafts is not None else None)
        )
        if accepted_rate is not None:
            output["observability:spec_accepted_token_rate"] = accepted_rate
        if draft_rate is not None:
            output["observability:spec_draft_token_rate"] = draft_rate
        if accepted is not None and drafts:
            output["observability:spec_acceptance_rate"] = accepted / drafts
        if accepted is not None and rounds:
            output["observability:spec_mean_accept_length"] = 1 + accepted / rounds
        return output


def _configured_poll_interval() -> float:
    try:
        return float(os.getenv("METRICS_POLL_INTERVAL", "1"))
    except ValueError:
        logger.warning("Invalid METRICS_POLL_INTERVAL; using 1 second")
        return 2.0


def _configured_history_retention() -> float:
    try:
        return max(300.0, min(float(os.getenv("METRICS_HISTORY_RETENTION_SECONDS", "172800")), 604800.0))
    except ValueError:
        logger.warning("Invalid METRICS_HISTORY_RETENTION_SECONDS; using 172800 seconds")
        return 172800.0


def _configured_cumulative_ttl() -> float:
    try:
        return max(30.0, min(float(os.getenv("CUMULATIVE_METRICS_TTL_SECONDS", "300")), 86400.0))
    except ValueError:
        logger.warning("Invalid CUMULATIVE_METRICS_TTL_SECONDS; using 300 seconds")
        return 300.0


store = MetricStore(
    interval=_configured_poll_interval(),
    history_retention_seconds=_configured_history_retention(),
    cumulative_ttl_seconds=_configured_cumulative_ttl(),
)
dcgm_store = DcgmStore(
    target_path=DCGM_TARGET_PATH,
    interval=_configured_poll_interval(),
    history_retention_seconds=_configured_history_retention(),
)


@app.on_event("startup")
async def startup() -> None:
    store.load_target()
    dcgm_store.load_target()
    await asyncio.gather(store.start(), dcgm_store.start())


@app.on_event("shutdown")
async def shutdown() -> None:
    await asyncio.gather(store.stop(), dcgm_store.stop())


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> HTMLResponse:
    return HTMLResponse((BASE_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/api/observability/target")
async def get_target() -> Dict[str, Any]:
    return {"url": store.target_url or "", "configured": bool(store.target_url)}


@app.put("/api/observability/target")
async def set_target(target: ObservabilityTargetRequest) -> Dict[str, str]:
    try:
        url = store.configure(target.url, target.api_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    store.save_target()
    asyncio.create_task(store.scrape())
    return {"status": "ok", "url": url}


@app.get("/api/observability/dcgm-target")
async def get_dcgm_target() -> Dict[str, Any]:
    return {"url": dcgm_store.target_url or "", "configured": bool(dcgm_store.target_url)}


@app.put("/api/observability/dcgm-target")
async def set_dcgm_target(target: DcgmTargetRequest) -> Dict[str, str]:
    try:
        url = dcgm_store.configure(target.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    dcgm_store.save_target()
    asyncio.create_task(dcgm_store.scrape())
    return {"status": "ok", "url": url}


@app.delete("/api/observability/dcgm-target")
async def delete_dcgm_target() -> Dict[str, str]:
    dcgm_store.clear_target()
    return {"status": "ok"}


@app.get("/api/vllm/metrics/all")
async def all_metrics() -> Dict[str, Any]:
    age = None
    source = "none"
    if store.last_scrape:
        age, source = round((datetime.now() - store.last_scrape).total_seconds(), 1), "prometheus"
    elif store.last_simulated:
        age, source = round((datetime.now() - store.last_simulated).total_seconds(), 1), "simulated"
    backend = "unknown"
    if any(key.startswith("sglang:") for key in store.metrics):
        backend = "sglang"
    elif any(key.startswith("vllm:") for key in store.metrics):
        backend = "vllm"
    elif source == "simulated":
        backend = "demo"
    return {"metrics": store.metrics, "scrape_age_seconds": age, "metric_count": len(store.metrics), "source": source,
            "run_mode": "remote" if store.target_url else "unknown", "backend": backend,
            "scrape_interval_seconds": store.interval,
            "history_retention_seconds": store.history_retention_seconds,
            "cumulative_groups": store.get_cumulative_groups(),
            "cumulative_ttl_seconds": store.cumulative_ttl_seconds,
            "dcgm": dcgm_store.status()}


@app.get("/api/vllm/metrics/history")
async def metrics_history(minutes: Optional[int] = None, seconds: Optional[int] = None) -> list:
    duration = seconds if seconds is not None else (minutes * 60 if minutes is not None else None)
    return store.get_history(duration)


@app.get("/api/observability/dcgm/history")
async def dcgm_history(minutes: Optional[int] = None, seconds: Optional[int] = None) -> list:
    duration = seconds if seconds is not None else (minutes * 60 if minutes is not None else None)
    return dcgm_store.get_history(duration)


@app.get("/api/vllm/metrics/history/summary")
async def history_summary() -> Dict[str, Any]:
    history = store.get_history()
    if not history:
        return {"total": 0, "oldest": None, "newest": None, "span_seconds": 0, "oldest_age_seconds": 0}
    oldest, newest = history[0], history[-1]
    old_time, new_time = datetime.fromisoformat(oldest["timestamp"]), datetime.fromisoformat(newest["timestamp"])
    return {"total": len(history), "oldest": oldest["timestamp"], "newest": newest["timestamp"],
            "span_seconds": round((new_time - old_time).total_seconds(), 1),
            "oldest_age_seconds": round((datetime.now() - old_time).total_seconds(), 1)}


@app.post("/api/vllm/metrics/simulate")
async def simulate_metrics(request: SimulateMetricsRequest) -> Dict[str, str]:
    values = request.model_dump(exclude_none=True)
    percent_keys = {"kv_cache_usage_perc", "prefix_cache_hit_rate", "gpu_cache_usage_perc", "cpu_cache_usage_perc"}
    key_map = {
        "kv_cache_usage_perc": "vllm:kv_cache_usage_perc", "prefix_cache_hit_rate": "vllm:prefix_cache_hit_rate",
        "num_preemptions": "vllm:num_preemptions", "num_requests_running": "vllm:num_requests_running",
        "num_requests_waiting": "vllm:num_requests_waiting", "prefix_cache_hits": "vllm:prefix_cache_hits",
        "prefix_cache_queries": "vllm:prefix_cache_queries", "gpu_cache_usage_perc": "vllm:gpu_cache_usage_perc",
        "kv_evictions": "vllm:kv_block_idle_before_evict_seconds_count",
        "cpu_cache_usage_perc": "vllm:cpu_cache_usage_perc", "spec_decode_accepted": "vllm:spec_decode_num_accepted_tokens",
        "spec_decode_draft": "vllm:spec_decode_num_draft_tokens",
    }
    store.metrics = {key_map[key]: {"value": value / 100 if key in percent_keys else value, "type": "gauge", "labels": ""}
                     for key, value in values.items()}
    now = datetime.now()
    store.cumulative_groups.clear()
    store._latest_group_intervals.clear()
    cumulative_samples = [
        {"key": key, "value": entry["value"], "labels": {"model_name": "demo", "engine_type": "demo"}}
        for key, entry in store.metrics.items()
    ]
    store._update_cumulative_groups([
        {
            **sample,
            "value": max(0, sample["value"] - 9) if "evict" in sample["key"] else sample["value"] * 0.9,
        }
        for sample in cumulative_samples
    ], now - timedelta(seconds=1))
    store._update_cumulative_groups(cumulative_samples, now)
    store.last_simulated, store.last_scrape = now, None
    for index in range(30):
        factor = 1 + random.uniform(-.12, .12)
        point = {key: {**entry, "value": max(0, entry["value"] * factor)} for key, entry in store.metrics.items()}
        store._append_snapshot(now - timedelta(seconds=29 - index), point)
    return {"status": "ok"}


@app.post("/api/vllm/metrics/simulate/reset")
async def reset_simulation() -> Dict[str, str]:
    store.metrics.clear()
    store.history.clear()
    store.history_archive.clear()
    store._last_archive_timestamp = None
    store.cumulative_groups.clear()
    store._latest_group_intervals.clear()
    store._histogram_series_state.clear()
    store._latest_histogram_intervals.clear()
    store.last_scrape = None
    store.last_simulated = None
    return {"status": "ok"}


def main(host: str = "0.0.0.0", port: int = 7860, reload: bool = False) -> None:
    uvicorn.run("playground.app:app", host=host, port=port, reload=reload)
