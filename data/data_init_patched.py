"""
Patched greenlet __init__.py for Nuitka standalone builds.
Falls back to pure-Python stub when C extension unavailable.
The async Playwright API imports greenlet but never calls it in async mode.
"""
try:
    from ._greenlet import (
        getcurrent,
        GreenletExit,
        error,
        greenlet,
        settrace,
        gettrace,
        settrace_all,
        gettrace_all,
        GREENLET_USE_CONTEXT_VARS,
    )
    # C extension loaded successfully
    __version__ = "3.0.0"

except (ImportError, OSError):
    # C extension unavailable (Nuitka standalone DLL issue)
    # Pure-Python stub — sufficient for async Playwright
    # Async Playwright imports greenlet but only uses it in sync mode

    __version__ = "3.0.0-stub"
    GREENLET_USE_CONTEXT_VARS = False

    class GreenletExit(BaseException):
        pass

    class error(Exception):
        pass

    _current = None

    class greenlet:
        """Pure-Python greenlet stub for async-only usage."""
        def __init__(self, run=None, parent=None):
            self.run   = run
            self.parent = parent
            self._dead  = False

        def switch(self, *args, **kwargs):
            if self.run:
                return self.run(*args, **kwargs)

        def throw(self, t=None, v=None, tb=None):
            raise (t or GreenletExit)()

        @property
        def dead(self):
            return self._dead

        @property
        def gr_frame(self):
            return None

        def __bool__(self):
            return not self._dead

    def getcurrent():
        return _current

    def settrace(cb):
        return None

    def gettrace():
        return None

    def settrace_all(cb):
        return None

    def gettrace_all():
        return None