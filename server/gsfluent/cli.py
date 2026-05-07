import argparse, webbrowser, uvicorn
from .server import create_app

def main():
    p = argparse.ArgumentParser(prog="gsfluent")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the workbench server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", default=8080, type=int)
    s.add_argument("--no-browser", action="store_true")
    args = p.parse_args()

    if args.cmd == "serve":
        if not args.no_browser:
            webbrowser.open(f"http://{args.host}:{args.port}")
        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
