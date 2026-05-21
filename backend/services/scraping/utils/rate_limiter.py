"""
Intelligent rate limiter for the scraping system.

Combines three independent limiting mechanisms:
  1. Minimum inter-request delay (simple sleep).
  2. Sliding-window counters for per-minute and per-hour caps.
  3. Token-bucket refill for burst control.
  4. Exponential or fixed backoff when consecutive errors are detected.

All state mutations are protected by a single threading.Lock so the instance
is safe to share across threads.
"""

import logging
import time
import threading
from typing import Any, Dict


class RateLimiter:
    """
    Advanced rate limiter for scrapers.

    Implements a token-bucket algorithm with sliding-window counters and
    exponential backoff. Thread-safe for concurrent scraping.

    Example::

        limiter = RateLimiter({
            "enabled": True,
            "delay_ms": 500,
            "max_requests_per_minute": 30,
            "max_requests_per_hour": 500,
            "backoff_strategy": "exponential",
            "max_backoff_ms": 60000,
            "bucket_capacity": 10,
            "bucket_refill_rate": 0.5,   # tokens per second
        })

        limiter.wait()          # blocks until the next request is allowed
        do_request()
        limiter.mark_success()  # or limiter.mark_error() on failure
    """

    def __init__(self, config: Dict[str, Any], logger: logging.Logger = None) -> None:
        """
        Args:
            config: Rate-limiting configuration dict. Keys:

                enabled (bool):
                    Master switch. When False, :meth:`wait` returns immediately.
                delay_ms (int):
                    Minimum milliseconds between any two consecutive requests.
                max_requests_per_minute (int):
                    Hard cap on requests within any 60-second window.
                max_requests_per_hour (int):
                    Hard cap on requests within any 3600-second window.
                backoff_strategy (str):
                    ``'exponential'`` → delay doubles each consecutive error.
                    ``'fixed'``       → delay grows linearly with error count.
                max_backoff_ms (int):
                    Ceiling for computed backoff delay.
                bucket_capacity (int):
                    Maximum tokens the bucket can hold (burst size).
                bucket_refill_rate (float):
                    Tokens added per second (sustain rate).

            logger: Optional logger; a default one is created if omitted.
        """
        self.enabled: bool = config.get("enabled", True)
        self.delay_ms: int = config.get("delay_ms", 1_000)
        self.max_rpm: int = config.get("max_requests_per_minute", 60)
        self.max_rph: int = config.get("max_requests_per_hour", 1_000)
        self.backoff_strategy: str = config.get("backoff_strategy", "exponential")
        self.max_backoff_ms: int = config.get("max_backoff_ms", 60_000)

        # Token-bucket parameters.
        self._bucket_capacity: float = float(config.get("bucket_capacity", self.max_rpm))
        self._bucket_tokens: float = self._bucket_capacity
        # Default: refill one token per (60 / max_rpm) seconds so steady-state
        # throughput equals max_rpm.
        default_refill = self.max_rpm / 60.0
        self._bucket_refill_rate: float = float(
            config.get("bucket_refill_rate", default_refill)
        )
        self._bucket_last_refill: float = time.monotonic()

        # Sliding-window counters.
        self._request_count_minute: int = 0
        self._request_count_hour: int = 0
        self._minute_window_start: float = time.monotonic()
        self._hour_window_start: float = time.monotonic()

        # Inter-request timing.
        self._last_request: float = 0.0

        # Backoff state.
        self._consecutive_errors: int = 0

        self._lock = threading.Lock()
        self.logger = logger or self._create_default_logger()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def wait(self) -> None:
        """
        Block until the next request is allowed.

        Enforcement order (all applied inside a single lock acquisition):
          1. Reset sliding windows if their period has elapsed.
          2. Consume one token from the bucket (refilling first); sleep if
             the bucket is empty.
          3. Enforce per-minute window cap.
          4. Enforce per-hour window cap.
          5. Enforce minimum inter-request delay.
          6. Apply backoff delay if there are consecutive errors.

        Returns immediately when ``enabled`` is False.
        """
        if not self.enabled:
            return

        with self._lock:
            self._reset_windows_if_needed()

            now = time.monotonic()

            # --- Token bucket ---
            self._refill_bucket(now)
            if self._bucket_tokens < 1.0:
                wait_s = (1.0 - self._bucket_tokens) / self._bucket_refill_rate
                self._sleep(wait_s, reason="token bucket empty")
                self._refill_bucket(time.monotonic())
            self._bucket_tokens -= 1.0

            # --- Per-minute cap ---
            if self._request_count_minute >= self.max_rpm:
                elapsed = time.monotonic() - self._minute_window_start
                remaining = 60.0 - elapsed
                if remaining > 0:
                    self._sleep(remaining, reason=f"per-minute cap ({self.max_rpm} rpm)")
                self._minute_window_start = time.monotonic()
                self._request_count_minute = 0

            # --- Per-hour cap ---
            if self._request_count_hour >= self.max_rph:
                elapsed = time.monotonic() - self._hour_window_start
                remaining = 3_600.0 - elapsed
                if remaining > 0:
                    self._sleep(remaining, reason=f"per-hour cap ({self.max_rph} rph)")
                self._hour_window_start = time.monotonic()
                self._request_count_hour = 0

            # --- Minimum inter-request delay ---
            since_last = time.monotonic() - self._last_request
            min_delay_s = self.delay_ms / 1_000.0
            if self._last_request and since_last < min_delay_s:
                self._sleep(min_delay_s - since_last, reason="min inter-request delay")

            # --- Backoff for consecutive errors ---
            if self._consecutive_errors > 0:
                backoff_s = self._calculate_backoff() / 1_000.0
                self._sleep(backoff_s, reason=f"backoff ({self._consecutive_errors} errors)")

            # Record this request.
            self._last_request = time.monotonic()
            self._request_count_minute += 1
            self._request_count_hour += 1

    def mark_success(self) -> None:
        """
        Signal that the last request succeeded.

        Resets the consecutive-error counter so backoff returns to zero.

        Examples:
            >>> limiter.wait()
            >>> response = fetch(url)
            >>> limiter.mark_success()
        """
        with self._lock:
            if self._consecutive_errors > 0:
                self.logger.debug(
                    "Resetting backoff after %d consecutive error(s)", self._consecutive_errors
                )
            self._consecutive_errors = 0

    def mark_error(self) -> None:
        """
        Signal that the last request failed.

        Increments the consecutive-error counter; the next :meth:`wait` call
        will include a backoff delay proportional to the error count.

        Examples:
            >>> limiter.wait()
            >>> try:
            ...     response = fetch(url)
            ...     limiter.mark_success()
            ... except Exception:
            ...     limiter.mark_error()
        """
        with self._lock:
            self._consecutive_errors += 1
            backoff_ms = self._calculate_backoff()
            self.logger.warning(
                "Error #%d recorded; next backoff will be %.0fms",
                self._consecutive_errors,
                backoff_ms,
            )

    def get_stats(self) -> Dict[str, Any]:
        """
        Return a snapshot of current rate-limiting state.

        Returns:
            Dict with keys:

            - ``requests_last_minute`` — requests counted in the current minute window.
            - ``requests_last_hour``   — requests counted in the current hour window.
            - ``consecutive_errors``   — unbroken error streak.
            - ``current_backoff_ms``   — backoff that would apply on next :meth:`wait`.
            - ``bucket_tokens``        — token-bucket fill level.
            - ``bucket_capacity``      — token-bucket maximum.

        Examples:
            >>> stats = limiter.get_stats()
            >>> print(stats["current_backoff_ms"])
            4000.0
        """
        with self._lock:
            self._reset_windows_if_needed()
            return {
                "requests_last_minute": self._request_count_minute,
                "requests_last_hour": self._request_count_hour,
                "consecutive_errors": self._consecutive_errors,
                "current_backoff_ms": self._calculate_backoff() if self._consecutive_errors else 0.0,
                "bucket_tokens": round(self._bucket_tokens, 2),
                "bucket_capacity": self._bucket_capacity,
            }

    def reset(self) -> None:
        """
        Reset all state (counters, backoff, bucket) to initial values.

        Useful between scraping sessions or when switching targets.
        """
        with self._lock:
            now = time.monotonic()
            self._bucket_tokens = self._bucket_capacity
            self._bucket_last_refill = now
            self._request_count_minute = 0
            self._request_count_hour = 0
            self._minute_window_start = now
            self._hour_window_start = now
            self._last_request = 0.0
            self._consecutive_errors = 0
        self.logger.debug("RateLimiter state reset")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calculate_backoff(self) -> float:
        """
        Compute the backoff delay in milliseconds based on current error count.

        Strategies:
          - ``'exponential'``: ``delay_ms * 2^(errors - 1)`` — doubles each error.
          - ``'fixed'``:       ``delay_ms * errors``          — linear growth.

        The result is capped at :attr:`max_backoff_ms`.

        Returns:
            Backoff delay in milliseconds (0.0 when no errors).
        """
        if self._consecutive_errors == 0:
            return 0.0

        if self.backoff_strategy == "exponential":
            raw = self.delay_ms * (2 ** (self._consecutive_errors - 1))
        else:  # fixed
            raw = self.delay_ms * self._consecutive_errors

        return float(min(raw, self.max_backoff_ms))

    def _refill_bucket(self, now: float) -> None:
        """
        Add tokens to the bucket proportional to elapsed time since last refill.

        Must be called inside the lock.
        """
        elapsed = now - self._bucket_last_refill
        new_tokens = elapsed * self._bucket_refill_rate
        if new_tokens > 0:
            self._bucket_tokens = min(
                self._bucket_capacity, self._bucket_tokens + new_tokens
            )
            self._bucket_last_refill = now

    def _reset_windows_if_needed(self) -> None:
        """
        Reset per-minute and per-hour counters when their windows have expired.

        Must be called inside the lock.
        """
        now = time.monotonic()

        if now - self._minute_window_start >= 60.0:
            self.logger.debug(
                "Minute window reset (had %d requests)", self._request_count_minute
            )
            self._request_count_minute = 0
            self._minute_window_start = now

        if now - self._hour_window_start >= 3_600.0:
            self.logger.debug(
                "Hour window reset (had %d requests)", self._request_count_hour
            )
            self._request_count_hour = 0
            self._hour_window_start = now

    def _sleep(self, seconds: float, reason: str = "") -> None:
        """
        Release the lock, sleep, then re-acquire.

        Releasing the lock during sleep lets other threads read stats or call
        ``mark_error`` / ``mark_success`` while this thread waits.
        """
        if seconds <= 0:
            return
        self.logger.debug("Rate limiting: sleeping %.3fs (%s)", seconds, reason)
        self._lock.release()
        try:
            time.sleep(seconds)
        finally:
            self._lock.acquire()

    @staticmethod
    def _create_default_logger() -> logging.Logger:
        logger = logging.getLogger("scraping.rate_limiter")
        if not logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
            )
            logger.addHandler(handler)
            logger.setLevel(logging.DEBUG)
        return logger
