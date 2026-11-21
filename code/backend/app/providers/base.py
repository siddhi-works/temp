"""Shared resilience for every external provider.

- `ProviderError` says whether a retry can help (timeouts, 429, 5xx) or not (4xx).
- `CircuitBreaker` stops hammering a provider that keeps failing, so the
  fallback chain moves on instantly instead of waiting on timeouts.
- `with_retries` retries transient errors with exponential backoff + jitter.
- `record_usage` logs every call (cost, latency, errors) for the budget guard
  and an honest cost slide.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx
from sqlalchemy import func, select

from ..db import sessionmaker
from ..models import ProviderUsage

log = logging.getLogger("suchana.providers")
T = TypeVar("T")


class ProviderError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = True, status: int | None = None):
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.retryable = retryable
        self.status = status


class ProviderNotConfigured(ProviderError):
    def __init__(self, provider: str, what: str):
        super().__init__(provider, f"not configured ({what} missing)", retryable=False)


class BudgetExceeded(ProviderError):
    def __init__(self, provider: str, spent: int, cap: int):
        super().__init__(provider, f"budget cap reached ({spent}/{cap} paise)", retryable=False)


def classify_http_error(provider: str, exc: Exception) -> ProviderError:
    """Map any HTTP/SDK exception to a ProviderError with the right retry flag."""
    if isinstance(exc, ProviderError):
        return exc
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, asyncio.TimeoutError, TimeoutError)):
        return ProviderError(provider, f"timeout/network: {exc}", retryable=True)
    status = getattr(exc, "status_code", None)
    if status is None and isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if status is None:
        status = getattr(exc, "code", None) if isinstance(getattr(exc, "code", None), int) else None
    if status is not None:
        retryable = status == 429 or status >= 500 or status == 408
        return ProviderError(provider, f"HTTP {status}: {str(exc)[:300]}", retryable=retryable, status=status)
    return ProviderError(provider, f"{type(exc).__name__}: {str(exc)[:300]}", retryable=True)


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, cooldown_seconds: float = 60.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.cooldown_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            if self.opened_at is None or self.state == "half_open":
                log.warning("circuit opened for %s after %d failures", self.name, self.failures)
            self.opened_at = time.monotonic()


_breakers: dict[str, CircuitBreaker] = {}


def breaker(name: str) -> CircuitBreaker:
    if name not in _breakers:
        _breakers[name] = CircuitBreaker(name)
    return _breakers[name]


def breaker_states() -> dict[str, str]:
    return {name: b.state for name, b in _breakers.items()}


def reset_breakers() -> None:
    _breakers.clear()


async def with_retries(
    provider: str,
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int = 3,
    base_delay: float = 0.8,
    timeout: float | None = 90.0,
) -> T:
    """Call fn with per-attempt timeout, retrying only transient failures."""
    cb = breaker(provider)
    if not cb.allow():
        raise ProviderError(provider, "circuit open (recent repeated failures)", retryable=True)
    last: ProviderError | None = None
    for attempt in range(1, attempts + 1):
        try:
            result = await (asyncio.wait_for(fn(), timeout) if timeout else fn())
            cb.success()
            return result
        except Exception as exc:
            err = classify_http_error(provider, exc)
            last = err
            if not err.retryable:
                # A request-specific refusal (bad input, closed 24h window, budget, missing key)
                # says nothing about the provider's health, so it must not open the circuit.
                if err.status is not None and err.status >= 500:
                    cb.failure()
                raise err from exc
            cb.failure()
            if attempt < attempts and cb.allow():
                delay = base_delay * (2 ** (attempt - 1)) * (0.7 + random.random() * 0.6)
                log.info("%s attempt %d failed (%s); retrying in %.1fs", provider, attempt, err, delay)
                await asyncio.sleep(delay)
            else:
                break
    assert last is not None
    raise last


async def record_usage(
    provider: str,
    operation: str,
    *,
    units: float = 0,
    cost_paise: int = 0,
    success: bool = True,
    latency_ms: int = 0,
    error: str | None = None,
) -> None:
    try:
        async with sessionmaker()() as s:
            s.add(
                ProviderUsage(
                    provider=provider,
                    operation=operation,
                    units=units,
                    cost_paise=cost_paise,
                    success=success,
                    latency_ms=latency_ms,
                    error=(error or "")[:1000] or None,
                )
            )
            await s.commit()
    except Exception:
        log.exception("failed to record provider usage")


async def spent_paise(provider: str) -> int:
    async with sessionmaker()() as s:
        total = await s.scalar(
            select(func.coalesce(func.sum(ProviderUsage.cost_paise), 0)).where(ProviderUsage.provider == provider)
        )
        return int(total or 0)


_budget_lock = asyncio.Lock()


async def reserve_budget(provider: str, cost_paise: int, cap_paise: int) -> None:
    """Fail before calling a paid API if the call would cross the cap.

    The lock makes check+record atomic within this process, so three parallel
    jobs cannot all pass the check at ₹54.50 and overspend together.
    """
    async with _budget_lock:
        spent = await spent_paise(provider)
        if spent + cost_paise > cap_paise:
            raise BudgetExceeded(provider, spent, cap_paise)
        await record_usage(provider, "reserve", cost_paise=cost_paise)


async def refund_budget(provider: str, cost_paise: int) -> None:
    """Undo a reservation when the paid call failed before being billed."""
    await record_usage(provider, "refund", cost_paise=-cost_paise)


class Timer:
    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.ms = int((time.perf_counter() - self.start) * 1000)
