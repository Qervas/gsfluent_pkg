from fastapi import FastAPI

# Re-exported: legacy `from gsfluent.server import PKG_ROOT` callers
# (gsfluent/core/runner.py, tools/*, tests) depend on this.
from ._paths import PKG_ROOT  # noqa: F401


def create_app() -> FastAPI:
    """Backward-compatible entry point. Delegates to composition.build_app().

    Existing callers (tests, ASGI servers) keep working unchanged.
    """
    from gsfluent.composition import build_app
    from gsfluent.config import AppConfig
    return build_app(AppConfig.from_env())
