from fastapi import FastAPI

from ._paths import PKG_ROOT  # re-exported; legacy `from ..server import PKG_ROOT` still works


def create_app() -> FastAPI:
    """Backward-compatible entry point. Delegates to composition.build_app().

    Existing callers (tests, ASGI servers) keep working unchanged.
    """
    from gsfluent.composition import build_app
    from gsfluent.config import AppConfig
    return build_app(AppConfig.from_env())
