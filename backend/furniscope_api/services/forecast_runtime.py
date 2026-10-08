"""Trusted adapter around the existing Sales Forecast V4 engine.

The legacy engine owns feature generation and model inference.  This adapter
keeps filesystem paths server-controlled, loads it lazily, serializes access to
its mutable in-memory state, and exposes JSON-safe deterministic results.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any


def coerce_training_date(value: Any) -> date | None:
    """asyncpg DATE binds need datetime.date, not ISO strings from meta.json."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value).strip()[:10])

from ..config import ApiSettings


class ForecastRuntime:
    def __init__(self, settings: ApiSettings, *, state_dir: Path | None = None,
                 model_version: str | None = None) -> None:
        self.settings = settings
        self.engine_root = Path(settings.forecast_engine_root).resolve()
        self.state_dir = (state_dir or Path(settings.forecast_state_dir)).resolve()
        self.model_version = model_version or settings.forecast_model_version
        self._service: Any | None = None
        self._lock = RLock()
        self._checksum_cache: tuple[tuple, str] | None = None

    def _required_paths(self) -> list[Path]:
        paths = [
            self.engine_path,
            self.state_dir / "meta.json",
            self.state_dir / "daily.pkl",
            self.state_dir / "weekly.pkl",
            self.state_dir / "day_model.pkl",
            self.state_dir / "week_model.pkl",
            self.state_dir / "encoders.pkl",
        ]
        for filename in ("model_manifest.json", "model_params.json"):
            if (self.state_dir / filename).is_file():
                paths.append(self.state_dir / filename)
        return paths

    @property
    def engine_path(self) -> Path:
        tenant_engine = self.state_dir / "forecast.py"
        return tenant_engine if tenant_engine.is_file() else self.engine_root / "forecast.py"

    def fingerprint(self) -> str:
        """Content SHA256; file metadata is only a cache invalidation hint."""
        paths = self._required_paths()
        signature = []
        for path in paths:
            if not path.is_file():
                raise RuntimeError(f"Forecast runtime artifact is missing: {path.name}")
            stat = path.stat()
            signature.append((path.name, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        signature = tuple(signature)
        if self._checksum_cache and self._checksum_cache[0] == signature:
            return self._checksum_cache[1]
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.name.encode() + b"\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    if chunk.startswith(b"version https://git-lfs.github.com/spec/"):
                        raise RuntimeError(f"Forecast artifact has not been downloaded: {path.name}")
                    digest.update(chunk)
        checksum = digest.hexdigest()
        self._checksum_cache = signature, checksum
        return checksum

    def metadata(self) -> dict[str, Any]:
        if not self.settings.forecast_enabled:
            return {"enabled": False, "ready": False}
        try:
            fingerprint = self.fingerprint()
            meta = json.loads((self.state_dir / "meta.json").read_text(encoding="utf-8"))
            model_card_path = self.state_dir.parent / "MODEL_CARD.json"
            model_card = (json.loads(model_card_path.read_text(encoding="utf-8"))
                          if model_card_path.is_file() else {})
            manifest_path = self.state_dir / "model_manifest.json"
            manifest = (json.loads(manifest_path.read_text(encoding="utf-8"))
                        if manifest_path.is_file() else {})
            return {
                "enabled": True,
                "ready": bool(meta.get("initialized")),
                "version": self.model_version,
                "engine": manifest.get("algorithm", "xgboost_lightgbm_v4"),
                "data_through": meta.get("last_date"),
                "sku_count": int(meta.get("n_skus", 0)),
                "state_checksum": fingerprint,
                "granularities": ["day", "week"],
                "trained_at": {key: value.get("trained_at") for key, value in
                               model_card.get("trained_models", {}).items()},
                "reported_backtest": manifest.get("evaluation") or (
                    {
                        **(model_card.get("recomputed_backtest") or {}),
                        "copied_from_docs": model_card.get("reported_backtest") or {},
                    }
                    if model_card.get("recomputed_backtest")
                    else (model_card.get("reported_backtest") or {})
                ),
                "data_quality": model_card.get("data_quality", {}),
            }
        except (OSError, ValueError, RuntimeError) as exc:
            return {"enabled": True, "ready": False, "error": str(exc)}

    def _load(self) -> Any:
        if not self.settings.forecast_enabled:
            raise RuntimeError("Forecast service is disabled")
        if self._service is not None:
            return self._service
        self.fingerprint()
        module_path = self.engine_path
        spec = importlib.util.spec_from_file_location("furniscope_sales_forecast_v4", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Unable to load Forecast V4 engine")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._service = module.ForecastService(self.state_dir)
        status = self._service.status()
        if not status.get("initialized") or not status.get("day_model") or not status.get("week_model"):
            self._service = None
            raise RuntimeError("Forecast V4 state is not fully initialized and trained")
        return self._service

    async def list_skus(self, site: str | None, limit: int) -> list[dict[str, Any]]:
        def execute() -> list[dict[str, Any]]:
            with self._lock:
                rows = self._load().list_skus(site.upper() if site else None)[:limit]
                return [{"sku": str(sku), "site": str(row_site), "history_weeks": int(weeks)}
                        for sku, row_site, weeks in rows]
        return await asyncio.to_thread(execute)

    async def retrain(self) -> None:
        """Retrain a reviewed tenant engine against its copied tenant state."""
        def execute() -> None:
            with self._lock:
                service = self._load()
                service.train(None)
                self._service = service
        await asyncio.to_thread(execute)

    async def predict(self, request: dict[str, Any]) -> dict[str, Any]:
        def execute() -> dict[str, Any]:
            with self._lock:
                service = self._load()
                effective_start = request.get("start_date")
                if not effective_start and request["granularity"] == "week":
                    data_through = self.metadata().get("data_through")
                    if data_through:
                        last_date = date.fromisoformat(data_through)
                        effective_start = (last_date + timedelta(
                            days=(7 - last_date.weekday()) % 7 or 7)).isoformat()
                summaries: list[dict[str, Any]] = []
                points: list[dict[str, Any]] = []
                for sku in request["skus"]:
                    for site in request["sites"]:
                        scenario = dict(request.get("scenario_config") or {})
                        user_reference = scenario.pop("reference_sku", None)
                        engine_sku = (user_reference
                                      or (request.get("engine_skus") or {}).get(sku)
                                      or sku)
                        result = service.predict(
                            engine_sku, site,
                            granularity=request["granularity"],
                            days=request["horizon"] if request["granularity"] == "day" else 7,
                            weeks=request["horizon"] if request["granularity"] == "week" else 1,
                            start_date=effective_start,
                            **scenario,
                        )
                        lower, upper = result["confidence_interval"]
                        reliability = result["reliability"]
                        provenance = {key: result[key] for key in
                                      ("method", "segment", "validation_status", "validation_scope", "data_through")
                                      if key in result}
                        if user_reference:
                            # Accuracy on the reference product is not accuracy on the target.
                            lower = upper = None
                            reliability = "D"
                            provenance.update(method="reference_sku", validation_status="unvalidated",
                                              validation_scope=None)
                        summaries.append({
                            "sku": sku, "site": site,
                            "total": float(result["weekly_total"]),
                            "daily_average": float(result["daily_avg"]),
                            "lower": float(lower) if lower is not None else None,
                            "upper": float(upper) if upper is not None else None,
                            "reliability": reliability, **provenance,
                        })
                        predictions = result["predictions"]
                        prediction_total = sum(float(item["sales"]) for item in predictions)
                        for item in predictions:
                            bucket_start = item.get("date") or item["week_start"]
                            point_lower = item.get("lower")
                            point_upper = item.get("upper")
                            if (request["granularity"] == "week" and result.get("validation_scope") != "window_total"
                                    and lower is not None
                                    and upper is not None and (point_lower is None or point_upper is None)):
                                # Backward compatibility for tenant engines created before
                                # weekly point intervals were added: allocate their aggregate
                                # interval over rows while preserving the reported total.
                                share = (float(item["sales"]) / prediction_total
                                         if prediction_total > 0
                                         else 1 / max(len(predictions), 1))
                                point_lower = round(float(lower) * share, 1)
                                point_upper = round(float(upper) * share, 1)
                            if user_reference:
                                point_lower = point_upper = None
                            points.append({
                                "sku": sku, "site": site, "bucket_start": bucket_start,
                                "predicted_sales": float(item["sales"]),
                                "lower": float(point_lower) if point_lower is not None else None,
                                "upper": float(point_upper) if point_upper is not None else None,
                                "reliability": reliability,
                            })
                total = round(sum(item["total"] for item in summaries), 1)
                upper_total = (round(sum(item["upper"] for item in summaries), 1)
                               if all(item["upper"] is not None for item in summaries) else None)
                safety_stock = max(0, round(upper_total - total)) if upper_total is not None else None
                return {
                    "summaries": summaries,
                    "points": points,
                    "metrics": {
                        "pair_count": len(summaries), "total_forecast": total,
                        "upper_total": upper_total, "safety_stock": safety_stock,
                        "recommended_production": round(total + safety_stock) if safety_stock is not None else None,
                        "planning_status": "error_band_available" if safety_stock is not None else "unvalidated",
                        "model_version": self.model_version,
                        "data_through": self.metadata().get("data_through"),
                        "forecast_start": effective_start or (points[0]["bucket_start"] if points else None),
                    },
                }
        return await asyncio.to_thread(execute)

    async def append_and_train(self, orders_path: Path,
                               inventory_path: Path | None = None) -> dict[str, Any]:
        """Append verified server-side workbooks and retrain both model granularities."""
        def execute() -> dict[str, Any]:
            with self._lock:
                service = self._load()
                metrics = service.append(str(orders_path),
                                         inventory_data=str(inventory_path) if inventory_path else None)
                if not metrics or not metrics.get("parsed_rows"):
                    raise ValueError("订单文件中没有可追加的有效销量数据")
                service.train(None)
                self._service = service
                return metrics
        return await asyncio.to_thread(execute)


class TenantForecastRuntimeRegistry:
    """Resolve trusted model artifacts selected by a tenant deployment row.

    Artifact locations are never accepted from an HTTP business request.  The
    database stores a server-managed URI, and this registry maps it to either
    the packaged shared model or a path below the configured tenant artifact
    root.
    """

    DEFAULT_URI = "server-managed://default"

    def __init__(self, settings: ApiSettings) -> None:
        self.settings = settings
        self.artifact_root = Path(settings.forecast_artifact_root).resolve()
        self._runtimes: dict[tuple[int, str, str, str], ForecastRuntime] = {}
        self._lock = RLock()

    def _state_dir(self, *, tenant_id: int, state_uri: str) -> Path:
        if state_uri == self.DEFAULT_URI:
            return Path(self.settings.forecast_state_dir).resolve()
        prefix = "server-managed://tenant/"
        if not state_uri.startswith(prefix):
            raise RuntimeError("Unsupported forecast artifact URI")
        relative = Path(state_uri.removeprefix(prefix))
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Invalid forecast artifact URI")
        expected_prefix = Path(str(tenant_id))
        if not relative.parts or relative.parts[0] != expected_prefix.name:
            raise RuntimeError("Forecast artifact does not belong to this tenant")
        resolved = (self.artifact_root / relative).resolve()
        if resolved != self.artifact_root and self.artifact_root not in resolved.parents:
            raise RuntimeError("Forecast artifact escapes the managed root")
        return resolved

    def state_dir(self, *, tenant_id: int, state_uri: str) -> Path:
        """Resolve a trusted persisted artifact URI for internal services."""
        return self._state_dir(tenant_id=tenant_id, state_uri=state_uri)

    def resolve(self, *, tenant_id: int, deployment: dict[str, Any]) -> ForecastRuntime:
        owner = deployment.get("owner_tenant_id")
        scope = deployment.get("model_scope")
        if scope == "tenant_private" and owner != tenant_id:
            raise RuntimeError("Private forecast model does not belong to this tenant")
        if scope == "shared_base" and owner is not None:
            raise RuntimeError("Shared forecast model cannot have a tenant owner")
        cache_tenant = 0 if scope == "shared_base" else tenant_id
        key = (cache_tenant, str(deployment["state_uri"]),
               str(deployment.get("version") or deployment.get("model_version")),
               str(deployment["state_checksum"]))
        with self._lock:
            runtime = self._runtimes.get(key)
            if runtime is None:
                runtime = ForecastRuntime(
                    self.settings,
                    state_dir=self._state_dir(tenant_id=tenant_id, state_uri=deployment["state_uri"]),
                    model_version=str(deployment.get("version") or deployment["model_version"]),
                )
                self._runtimes[key] = runtime
            return runtime

    def remember_verified(self, *, tenant_id: int, deployment: dict[str, Any],
                          runtime: ForecastRuntime) -> None:
        """Replace a provisional inspection cache key with the verified checksum key."""
        scope = deployment["model_scope"]
        cache_tenant = 0 if scope == "shared_base" else tenant_id
        key = (cache_tenant, str(deployment["state_uri"]), str(deployment["version"]),
               str(deployment["state_checksum"]))
        with self._lock:
            for old_key, value in list(self._runtimes.items()):
                if value is runtime:
                    self._runtimes.pop(old_key, None)
            self._runtimes[key] = runtime
