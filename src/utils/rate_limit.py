"""
Shared helpers for being a polite API citizen: rate limiting and retry-with-backoff.
Used by every ingestion script that hits an external source.
"""

import time
import functools


def retry_with_backoff(max_attempts: int = 3, base_delay: float = 2.0):
    """
    Decorator: retry a function on exception, waiting longer each time.
    Attempt 1 fails -> wait 2s. Attempt 2 fails -> wait 4s. Then give up.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        print(f"  attempt {attempt} failed ({e}), retrying in {delay:.0f}s...")
                        time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator


def polite_delay(seconds: float = 1.5):
    """Sleep between API calls so we don't hammer the source."""
    time.sleep(seconds)