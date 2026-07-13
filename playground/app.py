"""A focused, self-hosted observability dashboard for vLLM and SGLang metrics."""

import asyncio
import json
import logging
import os
import random
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
TARGET_PATH = Path.home() / ".vllm-observability" / "target.json"

app = FastAPI(title="vLLM Observability", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/assets", StaticFiles(directory=str(BASE_DIR / "assets")), name="assets")


class ObservabilityTargetRequest(BaseModel):
    """The vLLM or SGLang HTTP root; metrics are fetched from ``{url}/metrics``."""

    url: str = Field(min_length=1, max_length=2048)
    api_key: Optional[str] = Field(default=None, max_length=4096)


class SimulateMetricsRequest(BaseModel):
    kv_cache_usage_perc: Optional[float] = None
    prefix_cache_hit_rate: Optional[float] = None
    num_preemptions: Optional[float] = None
    num_requests_running: Optional[float] = None
    num_requests_waiting: Optional[float] = None
    prefix_cache_hits: Optional[float] = None
    prefix_cache_queries: Optional[float] = None
    gpu_cache_usage_perc: Optional[float] = None
    cpu_cache_usage_perc: Optional[float] = None
    spec_decode_accepted: Optional[float] = None
    spec_decode_draft: Optional[float] = None


class MetricStore:
    """Poll a vLLM or SGLang Prometheus endpoint and retain local history."""

    def __init__(self, interval: float = 2.0, history_size: int = 8640):
        self.interval = max(1.0, min(interval, 60.0))
        self.target_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.metrics: Dict[str, Dict[str, Any]] = {}
        self.history: deque = deque(maxlen=history_size)
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
                    parsed = self.parse_prometheus(await response.text())
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            if not self._warned:
                logger.warning("Cannot scrape vLLM metrics from %s: %s", self.target_url, exc)
                self._warned = True
            return False

        if not parsed:
            return False
        self.metrics = parsed
        self.last_scrape = datetime.now()
        self.last_simulated = None
        self._warned = False
        self._append_snapshot()
        return True

    @staticmethod
    def parse_prometheus(text: str) -> Dict[str, Dict[str, Any]]:
        types: Dict[str, str] = {}
        metrics: Dict[str, Dict[str, Any]] = {}
        buckets: Dict[str, list] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("# TYPE "):
                parts = line.split()
                if len(parts) >= 4:
                    types[parts[2]] = parts[3]
                continue
            if line.startswith("#"):
                continue
            sample = MetricStore._parse_sample(line)
            if not sample:
                continue
            name, labels, value = sample
            if not name.startswith(("vllm:", "sglang:")):
                continue
            base = MetricStore._base_name(name)
            if name.endswith("_bucket"):
                le = MetricStore._bucket_limit(labels)
                if le is not None:
                    buckets.setdefault(base, []).append((le, value))
                continue
            if name.endswith("_sum") or name.endswith("_count"):
                metrics[f"{base}{'_sum' if name.endswith('_sum') else '_count'}"] = {
                    "value": value, "type": "counter", "labels": labels
                }
                continue
            key = base if name.endswith("_total") else name
            metrics[key] = {"value": value, "type": types.get(base, "gauge"), "labels": labels}
        for base, values in buckets.items():
            percentiles = MetricStore._percentiles(values)
            if percentiles:
                metrics[base] = {"type": "histogram", "labels": "", **percentiles}
                for limit, count in values:
                    label = "+Inf" if limit == float("inf") else str(limit)
                    metrics[f"{base}_bucket_le_{label}"] = {
                        "value": count, "type": "histogram_bucket", "labels": f'le="{label}"'
                    }
        return metrics

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
        values = sorted(values, key=lambda item: item[0])
        total = values[-1][1] if values else 0
        if total <= 0:
            return {}
        output = {}
        for name, ratio in (("p50", .5), ("p95", .95), ("p99", .99)):
            threshold, previous_limit, previous_count = total * ratio, 0.0, 0.0
            for limit, count in values:
                if limit == float("inf"):
                    output[name] = previous_limit
                    break
                if count >= threshold:
                    fraction = 0 if count == previous_count else (threshold - previous_count) / (count - previous_count)
                    output[name] = previous_limit + fraction * (limit - previous_limit)
                    break
                previous_limit, previous_count = limit, count
        return output

    def _append_snapshot(self, timestamp: Optional[datetime] = None, values: Optional[Dict[str, float]] = None) -> None:
        snapshot = {"timestamp": (timestamp or datetime.now()).isoformat()}
        source = values or self.metrics
        for key, entry in source.items():
            if not isinstance(entry, dict):
                snapshot[key] = entry
                continue
            snapshot[key] = entry.get("value", entry.get("p50"))
            if entry.get("type") == "histogram":
                for percentile in ("p50", "p95", "p99"):
                    if entry.get(percentile) is not None:
                        snapshot[f"{key}::{percentile}"] = entry[percentile]
        derived = self._derive_rates(snapshot)
        snapshot.update(derived)
        self.history.append(snapshot)
        if derived and values is None:
            self.metrics.update({
                key: {"value": value, "type": "derived", "labels": ""}
                for key, value in derived.items()
            })

    def _derive_rates(self, snapshot: Dict[str, Any]) -> Dict[str, float]:
        """Derive short-interval token and vLLM spec-decode rates from counters."""
        if not self.history:
            return {}
        previous = self.history[-1]
        try:
            elapsed = (datetime.fromisoformat(snapshot["timestamp"]) - datetime.fromisoformat(previous["timestamp"])).total_seconds()
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

        output: Dict[str, float] = {}
        for prefix in ("vllm:", "sglang:"):
            prompt = delta(f"{prefix}prompt_tokens")
            generated = delta(f"{prefix}generation_tokens")
            if prompt is not None:
                output["observability:prompt_token_rate"] = prompt / elapsed
            if generated is not None:
                output["observability:generation_token_rate"] = generated / elapsed
            if prompt is not None or generated is not None:
                output["observability:total_token_rate"] = ((prompt or 0) + (generated or 0)) / elapsed

        accepted = delta("vllm:spec_decode_num_accepted_tokens")
        drafts = delta("vllm:spec_decode_num_draft_tokens")
        rounds = delta("vllm:spec_decode_num_drafts")
        if accepted is not None:
            output["observability:spec_accepted_token_rate"] = accepted / elapsed
        if drafts is not None:
            output["observability:spec_draft_token_rate"] = drafts / elapsed
        if accepted is not None and drafts:
            output["observability:spec_acceptance_rate"] = accepted / drafts
        if accepted is not None and rounds:
            output["observability:spec_mean_accept_length"] = 1 + accepted / rounds
        return output


def _configured_poll_interval() -> float:
    try:
        return float(os.getenv("METRICS_POLL_INTERVAL", "2"))
    except ValueError:
        logger.warning("Invalid METRICS_POLL_INTERVAL; using 2 seconds")
        return 2.0


store = MetricStore(interval=_configured_poll_interval())


@app.on_event("startup")
async def startup() -> None:
    store.load_target()
    await store.start()


@app.on_event("shutdown")
async def shutdown() -> None:
    await store.stop()


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
            "scrape_interval_seconds": store.interval}


@app.get("/api/vllm/metrics/history")
async def metrics_history(minutes: Optional[int] = None, seconds: Optional[int] = None) -> list:
    duration = seconds if seconds is not None else (minutes * 60 if minutes is not None else None)
    if duration is None:
        return list(store.history)
    cutoff = datetime.now() - timedelta(seconds=max(duration, 0))
    return [point for point in store.history if datetime.fromisoformat(point["timestamp"]) >= cutoff]


@app.get("/api/vllm/metrics/history/summary")
async def history_summary() -> Dict[str, Any]:
    if not store.history:
        return {"total": 0, "oldest": None, "newest": None, "span_seconds": 0, "oldest_age_seconds": 0}
    oldest, newest = store.history[0], store.history[-1]
    old_time, new_time = datetime.fromisoformat(oldest["timestamp"]), datetime.fromisoformat(newest["timestamp"])
    return {"total": len(store.history), "oldest": oldest["timestamp"], "newest": newest["timestamp"],
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
        "cpu_cache_usage_perc": "vllm:cpu_cache_usage_perc", "spec_decode_accepted": "vllm:spec_decode_num_accepted_tokens",
        "spec_decode_draft": "vllm:spec_decode_num_draft_tokens",
    }
    store.metrics = {key_map[key]: {"value": value / 100 if key in percent_keys else value, "type": "gauge", "labels": ""}
                     for key, value in values.items()}
    now = datetime.now()
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
    store.last_scrape = None
    store.last_simulated = None
    return {"status": "ok"}


def main(host: str = "0.0.0.0", port: int = 7860, reload: bool = False) -> None:
    uvicorn.run("playground.app:app", host=host, port=port, reload=reload)
