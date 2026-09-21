"""Bounded parallel execution for synchronous travel providers.

The existing adapters intentionally remain synchronous.  This module gives
the workflow a small seam for concurrency, retry classification and public
provider diagnostics without making provider payloads part of the orchestration
contract.
"""

from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

from agents.schemas import ProviderMeta


CN_TZ = ZoneInfo("Asia/Shanghai")
MAX_PROVIDER_ATTEMPTS = 2


@dataclass(slots=True)
class ProviderTaskResult:
    provider: str
    value: Any = None
    meta: ProviderMeta = field(default_factory=lambda: ProviderMeta(provider="unknown"))
    errors: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderOrchestrationResult:
    results: dict[str, ProviderTaskResult] = field(default_factory=dict)

    def result(self, provider: str) -> ProviderTaskResult:
        return self.results.get(
            provider,
            ProviderTaskResult(
                provider=provider,
                meta=ProviderMeta(provider=provider, status="not_requested"),
            ),
        )

    @property
    def provider_meta(self) -> dict[str, ProviderMeta]:
        return {name: item.meta for name, item in self.results.items()}


def is_retryable_provider_error(error: BaseException) -> bool:
    """Classify only transient failures as safe for one bounded retry."""

    failure_kind = str(getattr(error, "failure_kind", "") or "").lower()
    if failure_kind in {"network", "timeout", "temporary", "rate_limit", "transient"}:
        return True
    if failure_kind in {"invalid_input", "invalid_config", "not_configured", "unauthorized", "schema_changed"}:
        return False
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return True
    text = str(error or "").lower()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "service unavailable",
            "http 429",
            "http 502",
            "http 503",
            "http 504",
            "qps",
        )
    )


def _failure_code(error: BaseException) -> str:
    failure_kind = str(getattr(error, "failure_kind", "") or "").strip()
    if failure_kind:
        return failure_kind
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, (ConnectionError, OSError)):
        return "network"
    return type(error).__name__.lower()


def _log_provider(activity_logger: Callable | None, provider: str, state: str, detail: str) -> None:
    if not activity_logger:
        return
    try:
        activity_logger(
            "provider",
            f"{provider} 数据源",
            detail,
            state,
            provider=provider,
        )
    except TypeError:
        try:
            activity_logger("provider", f"{provider} 数据源", detail, state)
        except TypeError:
            activity_logger("provider", f"{provider} 数据源", detail)


def _status_for_value(value: Any, errors: list[str]) -> str:
    if errors:
        return "partial" if value else "failed"
    if value is None:
        return "empty"
    try:
        return "success" if len(value) > 0 else "empty"
    except TypeError:
        return "success" if value else "empty"


def _run_provider(
    provider: str,
    operation: Callable[[], Any],
    *,
    max_attempts: int = MAX_PROVIDER_ATTEMPTS,
    backoff_seconds: float = 0.15,
    activity_logger: Callable | None = None,
) -> ProviderTaskResult:
    started = time.monotonic()
    errors: list[str] = []
    value: Any = None
    attempts = 0
    retryable = False
    error_code = ""
    for attempt in range(1, max(1, min(MAX_PROVIDER_ATTEMPTS, max_attempts)) + 1):
        attempts = attempt
        _log_provider(activity_logger, provider, "running", f"第 {attempt} 次请求")
        try:
            value = operation()
            provider_errors = list(getattr(value, "errors", []) or [])
            # A transient failure from an earlier attempt is not a partial
            # provider result once the retry returned clean data.  Preserve
            # only provider-reported row errors from the successful payload.
            errors = [str(item) for item in provider_errors if str(item)]
            retryable = False
            error_code = ""
            _log_provider(activity_logger, provider, "completed", f"返回结果，耗时 {int((time.monotonic() - started) * 1000)}ms")
            break
        except Exception as exc:  # provider boundary: isolate one source
            value = None
            retryable = is_retryable_provider_error(exc)
            error_code = _failure_code(exc)
            errors.append(type(exc).__name__)
            if retryable and attempt < max_attempts:
                _log_provider(activity_logger, provider, "retrying", f"{error_code}，将有限重试一次")
                if backoff_seconds > 0:
                    time.sleep(backoff_seconds * attempt)
                continue
            _log_provider(activity_logger, provider, "failed", f"{error_code}，本轮不再重试")
            break

    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    retrieved_at = datetime.now(CN_TZ).isoformat(timespec="seconds")
    valid_until = (datetime.now(CN_TZ) + timedelta(minutes=10)).isoformat(timespec="seconds")
    status = _status_for_value(value, errors)
    if value is None and error_code == "not_configured":
        status = "not_configured"
    return ProviderTaskResult(
        provider=provider,
        value=value,
        errors=list(dict.fromkeys(errors)),
        meta=ProviderMeta(
            provider=provider,
            status=status,
            retryable=retryable and status == "failed",
            error_code=error_code,
            attempts=attempts,
            latency_ms=elapsed_ms,
            retrieved_at=retrieved_at,
            valid_until=valid_until,
        ),
    )


def run_provider_orchestrator(
    operations: dict[str, Callable[[], Any]],
    *,
    max_workers: int = 4,
    activity_logger: Callable | None = None,
    cancellation_check: Callable | None = None,
) -> ProviderOrchestrationResult:
    """Run independent provider calls in parallel with isolated failures."""

    if not operations:
        return ProviderOrchestrationResult()
    workers = max(1, min(4, int(max_workers), len(operations)))
    result = ProviderOrchestrationResult()
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="travel-provider") as pool:
        futures: dict[Future, str] = {
            pool.submit(
                _run_provider,
                name,
                operation,
                activity_logger=activity_logger,
            ): name
            for name, operation in operations.items()
        }
        for future in as_completed(futures):
            if cancellation_check:
                cancellation_check()
            name = futures[future]
            try:
                result.results[name] = future.result()
            except Exception as exc:
                # A worker failure must never erase other completed providers.
                result.results[name] = ProviderTaskResult(
                    provider=name,
                    errors=[type(exc).__name__],
                    meta=ProviderMeta(
                        provider=name,
                        status="failed",
                        retryable=is_retryable_provider_error(exc),
                        error_code=_failure_code(exc),
                        attempts=1,
                    ),
                )
    return result
