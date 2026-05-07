import argparse
import threading
import webbrowser

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="gsfluent")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    serve_parser = subparsers.add_parser("serve", help="run the workbench server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=8080, type=int)
    serve_parser.add_argument("--no-browser", action="store_true")
    serve_parser.add_argument("--reload", action="store_true",
                              help="reload server on code changes (dev only)")
    args = parser.parse_args()

    if args.cmd == "serve":
        if not args.no_browser:
            # Defer the browser-open until after uvicorn has bound the port.
            threading.Timer(
                0.5,
                lambda: webbrowser.open(f"http://{args.host}:{args.port}"),
            ).start()
        uvicorn.run(
            "gsfluent.server:create_app",
            host=args.host,
            port=args.port,
            log_level="info",
            factory=True,
            reload=args.reload,
        )
