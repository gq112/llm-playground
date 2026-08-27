"""Direct dcgm-exporter polling and conservative GPU bottleneck diagnosis."""

import asyncio
import json
import logging
import math
import os
import re
from collections import Counter, deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp


logger = logging.getLogger(__name__)


class DcgmStore:
    """Poll a dcgm-exporter endpoint and retain per-GPU history locally."""

    _LABEL_PATTERN = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*"((?:\\.|[^"\\])*)"')
    _METRICS = {
        "DCGM_FI_DEV_GPU_UTIL": ("gpu_util", "percent"),
        "DCGM_FI_DEV_GPU_UTIL_RATIO": ("gpu_util", "ratio"),
        "DCGM_FI_DEV_MEM_COPY_UTIL": ("memory_copy_util", "percent"),
        "DCGM_FI_DEV_DEC_UTIL": ("decoder_util", "percent"),
        "DCGM_FI_DEV_ENC_UTIL": ("encoder_util", "percent"),
        "DCGM_FI_PROF_GR_ENGINE_ACTIVE": ("gr_engine_active", "ratio"),
        "DCGM_FI_PROF_GR_ENGINE_UTIL_RATIO": ("gr_engine_active", "ratio"),
        "DCGM_FI_PROF_SM_ACTIVE": ("sm_active", "ratio"),
        "DCGM_FI_PROF_SM_UTIL_RATIO": ("sm_active", "ratio"),
        "DCGM_FI_PROF_SM_OCCUPANCY": ("sm_occupancy", "ratio"),
        "DCGM_FI_PROF_SM_OCCUPANCY_RATIO": ("sm_occupancy", "ratio"),
        "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE": ("tensor_active", "ratio"),
        "DCGM_FI_PROF_TENSOR_UTIL_RATIO": ("tensor_active", "ratio"),
        "DCGM_FI_PROF_DRAM_ACTIVE": ("dram_active", "ratio"),
        "DCGM_FI_PROF_DRAM_UTIL_RATIO": ("dram_active", "ratio"),
        "DCGM_FI_DEV_FB_USED": ("fb_used_mib", "number"),
        "DCGM_FI_DEV_FB_FREE": ("fb_free_mib", "number"),
        "DCGM_FI_DEV_POWER_USAGE": ("power_watts", "number"),
        "DCGM_FI_DEV_BOARD_POWER_WATTS": ("power_watts", "number"),
        "DCGM_FI_DEV_SM_CLOCK": ("sm_clock_mhz", "number"),
        "DCGM_FI_DEV_MEM_CLOCK": ("memory_clock_mhz", "number"),
        "DCGM_FI_DEV_GPU_TEMP": ("temperature_c", "number"),
        "DCGM_FI_DEV_GPU_TEMP_CELSIUS": ("temperature_c", "number"),
        "DCGM_FI_DEV_MEMORY_TEMP": ("memory_temperature_c", "number"),
        "DCGM_FI_DEV_MEMORY_TEMP_CELSIUS": ("memory_temperature_c", "number"),
        "DCGM_FI_PROF_PCIE_TX_BYTES": ("pcie_tx_bytes_per_second", "number"),
        "DCGM_FI_PROF_PCIE_RX_BYTES": ("pcie_rx_bytes_per_second", "number"),
        "DCGM_FI_DEV_NVLINK_BANDWIDTH_TOTAL": ("nvlink_bytes_per_second", "number"),
        "DCGM_FI_DEV_NVLINK_THROUGHPUT_TOTAL": ("nvlink_bytes_per_second", "number"),
        "DCGM_FI_DEV_XID_ERRORS": ("xid_error", "number"),
        "DCGM_FI_DEV_XID_ERROR": ("xid_error", "number"),
    }
    REQUIRED_DIAGNOSTIC_FIELDS = ("sm_active", "tensor_active", "dram_active")

    def __init__(
        self,
        target_path: Path,
        interval: float = 1.0,
        history_size: int = 8640,
        history_retention_seconds: float = 172800.0,
        archive_interval_seconds: float = 30.0,
    ) -> None:
        self.target_path = target_path
        self.interval = max(1.0, min(interval, 60.0))
        self.history_retention_seconds = max(300.0, history_retention_seconds)
        self.archive_interval_seconds = max(self.interval, archive_interval_seconds)
        self.target_url: Optional[str] = None
        self.gpus: Dict[str, Dict[str, Any]] = {}
        self.history: deque = deque(maxlen=history_size)
        archive_size = math.ceil(self.history_retention_seconds / self.archive_interval_seconds) + 1
        self.history_archive: deque = deque(maxlen=archive_size)
        self.last_scrape: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self._last_archive_timestamp: Optional[datetime] = None
        self._task: Optional[asyncio.Task] = None
        self._warned = False

    def configure(self, url: str) -> str:
        normalized = url.strip().rstrip("/")
        if normalized.lower().endswith("/metrics"):
            normalized = normalized[:-8].rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("DCGM source must be an absolute http(s) URL")
        self.target_url = normalized
        self._clear_samples()
        return normalized

    def clear_target(self) -> None:
        self.target_url = None
        self._clear_samples()
        try:
            self.target_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove saved DCGM target: %s", exc)

    def _clear_samples(self) -> None:
        self.gpus.clear()
        self.history.clear()
        self.history_archive.clear()
        self.last_scrape = None
        self.last_error = None
        self._last_archive_timestamp = None
        self._warned = False

    def load_target(self) -> None:
        url = os.getenv("DCGM_METRICS_URL", "").strip()
        if not url and self.target_path.exists():
            try:
                saved = json.loads(self.target_path.read_text(encoding="utf-8"))
                url = str(saved.get("url") or "").strip()
            except (OSError, ValueError, TypeError):
                logger.warning("Ignoring unreadable saved DCGM target")
        if url:
            try:
                self.configure(url)
            except ValueError as exc:
                logger.warning("Ignoring invalid DCGM target: %s", exc)

    def save_target(self) -> None:
        if not self.target_url:
            return
        try:
            self.target_path.parent.mkdir(parents=True, exist_ok=True)
            self.target_path.write_text(json.dumps({"url": self.target_url}), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not save DCGM target: %s", exc)

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
        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.target_url}/metrics") as response:
                    if response.status != 200:
                        raise RuntimeError(f"/metrics returned HTTP {response.status}")
                    parsed = self.parse_prometheus(await response.text())
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as exc:
            self.gpus.clear()
            self.last_scrape = None
            self.last_error = str(exc)
            if not self._warned:
                logger.warning("Cannot scrape DCGM metrics from %s: %s", self.target_url, exc)
                self._warned = True
            return False

        if not parsed:
            self.gpus.clear()
            self.last_scrape = None
            self.last_error = "No supported per-GPU DCGM metrics were exposed"
            return False

        now = datetime.now()
        self.gpus = parsed
        self.last_scrape = now
        self.last_error = None
        self._warned = False
        self._append_snapshot(now)
        return True

    @classmethod
    def parse_prometheus(cls, text: str) -> Dict[str, Dict[str, Any]]:
        gpus: Dict[str, Dict[str, Any]] = {}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            sample = cls._parse_sample(line)
            if not sample:
                continue
            name, raw_labels, value = sample
            spec = cls._METRICS.get(name)
            if spec is None or not math.isfinite(value) or abs(value) >= 1e15:
                continue
            labels = cls._parse_labels(raw_labels)
            gpu_id = labels.get("gpu")
            if gpu_id is None:
                continue
            field, scale = spec
            normalized = cls._normalize_value(value, scale)
            if normalized is None:
                continue
            gpu = gpus.setdefault(gpu_id, {
                "gpu": gpu_id,
                "uuid": labels.get("UUID") or labels.get("uuid") or "",
                "device": labels.get("device") or "",
                "model": labels.get("modelName") or labels.get("model") or "",
                "hostname": labels.get("Hostname") or labels.get("hostname") or "",
                "metrics": {},
            })
            gpu["metrics"][field] = normalized

        for gpu in gpus.values():
            metrics = gpu["metrics"]
            used, free = metrics.get("fb_used_mib"), metrics.get("fb_free_mib")
            if used is not None and free is not None and used + free > 0:
                metrics["fb_total_mib"] = used + free
                metrics["fb_usage"] = used / (used + free)
            tx = metrics.get("pcie_tx_bytes_per_second")
            rx = metrics.get("pcie_rx_bytes_per_second")
            if tx is not None or rx is not None:
                metrics["pcie_total_bytes_per_second"] = (tx or 0.0) + (rx or 0.0)
        return gpus

    @staticmethod
    def _normalize_value(value: float, scale: str) -> Optional[float]:
        if value < 0:
            return None
        if scale == "percent":
            return min(1.0, value / 100.0)
        if scale == "ratio":
            return min(1.0, value)
        return value

    @staticmethod
    def _parse_sample(line: str) -> Optional[tuple]:
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

    @classmethod
    def _parse_labels(cls, labels: str) -> Dict[str, str]:
        parsed = {}
        for match in cls._LABEL_PATTERN.finditer(labels):
            parsed[match.group(1)] = match.group(2).replace(r'\"', '"').replace(r"\\", "\\")
        return parsed

    @classmethod
    def diagnose(cls, metrics: Dict[str, float]) -> Dict[str, Any]:
        signals = {key: metrics[key] for key in cls.REQUIRED_DIAGNOSTIC_FIELDS if key in metrics}
        missing = [key for key in cls.REQUIRED_DIAGNOSTIC_FIELDS if key not in metrics]
        if "sm_active" in missing:
            return {
                "kind": "insufficient_data",
                "label": "诊断信号不足",
                "confidence": "none",
                "severity": "neutral",
                "signals": signals,
                "missing": missing,
                "reason": "需要启用 DCGM_FI_PROF_SM_ACTIVE 才能可靠判断 GPU 是否被持续占用。",
            }

        sm = metrics["sm_active"]
        tensor = metrics.get("tensor_active")
        dram = metrics.get("dram_active")
        if sm < 0.5:
            kind, label, confidence, severity = "underutilized", "GPU 未吃满", "high", "warning"
            reason = "SM Active 低于 50%，优先排查请求量、batch、调度、CPU 或 kernel gap。"
        elif sm >= 0.8 and dram is not None and dram >= 0.8 and (tensor is None or tensor < 0.65):
            kind, label, confidence, severity = "memory_bound", "显存带宽受限倾向", "medium", "danger"
            reason = "SM 持续活跃且显存接口接近饱和，Tensor 活跃度相对较低。"
        elif sm >= 0.8 and tensor is not None and tensor >= 0.65 and (dram is None or dram < 0.8):
            kind, label, confidence, severity = "compute_bound", "计算受限倾向", "medium", "danger"
            reason = "SM 与 Tensor 管线持续活跃，显存接口压力相对较低。"
        elif sm >= 0.8 and tensor is not None and tensor >= 0.65 and dram is not None and dram >= 0.8:
            kind, label, confidence, severity = "mixed_saturation", "计算与显存均繁忙", "medium", "danger"
            reason = "SM、Tensor 与显存接口同时处于高活跃区间，需结合吞吐变化进一步定位。"
        else:
            kind, label, confidence, severity = "mixed", "混合或阶段性负载", "low", "neutral"
            reason = "当前信号未形成稳定的计算受限或显存受限特征。"
        return {
            "kind": kind,
            "label": label,
            "confidence": confidence,
            "severity": severity,
            "signals": signals,
            "missing": missing,
            "reason": reason,
        }

    def _append_snapshot(self, observed_at: datetime) -> None:
        snapshot = {
            "timestamp": observed_at.isoformat(),
            "gpus": {gpu_id: dict(gpu["metrics"]) for gpu_id, gpu in self.gpus.items()},
        }
        self.history.append(snapshot)
        if (
            self._last_archive_timestamp is None
            or observed_at < self._last_archive_timestamp
            or (observed_at - self._last_archive_timestamp).total_seconds() >= self.archive_interval_seconds
        ):
            if self._last_archive_timestamp is not None and observed_at < self._last_archive_timestamp:
                self.history_archive.clear()
            self.history_archive.append({
                "timestamp": snapshot["timestamp"],
                "gpus": {gpu_id: dict(values) for gpu_id, values in snapshot["gpus"].items()},
            })
            self._last_archive_timestamp = observed_at
        cutoff = observed_at - timedelta(seconds=self.history_retention_seconds)
        while self.history_archive and datetime.fromisoformat(self.history_archive[0]["timestamp"]) < cutoff:
            self.history_archive.popleft()

    def get_history(self, seconds: Optional[float] = None, now: Optional[datetime] = None) -> list:
        merged = {point["timestamp"]: point for point in self.history_archive}
        merged.update({point["timestamp"]: point for point in self.history})
        points = sorted(merged.values(), key=lambda point: point["timestamp"])
        if seconds is None:
            return points
        cutoff = (now or datetime.now()) - timedelta(seconds=max(seconds, 0))
        return [point for point in points if datetime.fromisoformat(point["timestamp"]) >= cutoff]

    @staticmethod
    def _gpu_sort_key(gpu_id: str) -> tuple:
        try:
            return 0, int(gpu_id)
        except ValueError:
            return 1, gpu_id

    def status(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        observed_at = now or datetime.now()
        gpu_rows = []
        for gpu_id in sorted(self.gpus, key=self._gpu_sort_key):
            gpu = self.gpus[gpu_id]
            gpu_rows.append({**gpu, "diagnosis": self.diagnose(gpu["metrics"])})

        ratios = ("gpu_util", "sm_active", "sm_occupancy", "tensor_active", "dram_active", "fb_usage")
        totals = ("fb_used_mib", "power_watts", "pcie_tx_bytes_per_second", "pcie_rx_bytes_per_second")
        summary: Dict[str, Any] = {}
        for field in ratios:
            values = [gpu["metrics"][field] for gpu in gpu_rows if field in gpu["metrics"]]
            if values:
                summary[field] = sum(values) / len(values)
        for field in totals:
            values = [gpu["metrics"][field] for gpu in gpu_rows if field in gpu["metrics"]]
            if values:
                summary[field] = sum(values)
        summary["diagnoses"] = dict(Counter(gpu["diagnosis"]["kind"] for gpu in gpu_rows))

        age = None
        if self.last_scrape:
            age = max(0.0, (observed_at - self.last_scrape).total_seconds())
        available_fields = sorted({field for gpu in gpu_rows for field in gpu["metrics"]})
        return {
            "configured": bool(self.target_url),
            "url": self.target_url or "",
            "available": bool(gpu_rows),
            "scrape_age_seconds": round(age, 1) if age is not None else None,
            "scrape_interval_seconds": self.interval,
            "history_retention_seconds": self.history_retention_seconds,
            "gpu_count": len(gpu_rows),
            "available_fields": available_fields,
            "missing_diagnostic_fields": [
                field for field in self.REQUIRED_DIAGNOSTIC_FIELDS if field not in available_fields
            ],
            "last_error": self.last_error,
            "summary": summary,
            "gpus": gpu_rows,
        }
