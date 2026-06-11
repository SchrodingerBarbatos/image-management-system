"""Shared SQLite lock retry utilities.

Extracted from versioning.py's _is_sqlite_locked / retry loop so that
all write paths can share the same safe retry logic.

Usage:
    from db_retry import with_sqlite_lock_retry

    @with_sqlite_lock_retry()
    def my_write_function():
        session.add(...)
        session.commit()
"""

import time
import logging
from functools import wraps

_log = logging.getLogger(__name__)

_DEFAULT_MAX_ATTEMPTS = 5
_DEFAULT_DELAY = 0.5  # seconds


def _resolve_default_session():
    """Return the module-level session from models, resolved at call time.

    This is intentionally NOT a module-level binding so that monkeypatching
    ``versioning.session`` (or ``models.session``) in tests is visible to
    the retry decorator.
    """
    from models import session as sess
    return sess


def _is_sqlite_locked(exc):
    """Check whether an exception is a SQLite 'database is locked' error.

    Covers both SQLAlchemy-wrapped and raw sqlite3 exceptions.
    """
    # SQLAlchemy wraps the original exception
    orig = getattr(exc, 'orig', None)
    if orig is not None:
        # sqlite3 >= 3.7.11 exposes sqlite_errorcode / sqlite_errorname
        if getattr(orig, 'sqlite_errorcode', None) == 5:
            return True
        if getattr(orig, 'sqlite_errorname', '') == 'SQLITE_BUSY':
            return True
    # Fallback: message-based detection (covers edge cases)
    return 'database is locked' in str(exc).lower()


def with_sqlite_lock_retry(max_attempts=_DEFAULT_MAX_ATTEMPTS,
                           delay=_DEFAULT_DELAY,
                           sess=None,
                           get_sess=None):
    """Decorator that retries the wrapped function on SQLite lock errors.

    On each retry the session is rolled back to clear the failed transaction,
    then the function is called again from a clean state.

    Args:
        max_attempts: maximum number of attempts (including the first call).
        delay: seconds to wait between retries (constant, not exponential).
        sess: optional session override; defaults to the module-level session.
              Pass sess=some_session when the caller uses a thread-local session.
        get_sess: optional callable returning the session at call time.
                  When provided, takes precedence over sess and the default.
                  Useful for background task handlers that use thread-local sessions:
                      get_sess=_get_thread_session.

    Usage:
        @with_sqlite_lock_retry()
        def do_something():
            session.add(...)
            session.commit()

        # With explicit session:
        @with_sqlite_lock_retry(sess=my_thread_sess)
        def do_something():
            my_thread_sess.commit()

        # With lazy session (background threads):
        @with_sqlite_lock_retry(get_sess=_get_thread_session)
        def my_handler(task_id):
            ...
    """
    def _resolve_sess():
        if get_sess is not None:
            return get_sess()
        if sess is not None:
            return sess
        return _resolve_default_session()

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    if not _is_sqlite_locked(e) or attempt == max_attempts:
                        raise
                    target = _resolve_sess()
                    try:
                        target.rollback()
                    except Exception:
                        pass  # rollback failure is non-fatal; session may already be clean
                    _log.warning(
                        "%s: SQLite locked, retry %d/%d (delay=%.1fs)",
                        fn.__name__, attempt, max_attempts, delay
                    )
                    time.sleep(delay)
        return wrapper
    return decorator
