"""Lance l'API (et le front compilé s'il existe) : python -m biflow.api [--port 8000]."""

from __future__ import annotations

import argparse

import uvicorn

from biflow.common.logging import setup_logging


def main() -> None:
    p = argparse.ArgumentParser(description="API BIFlow + front React")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="rechargement auto (développement)")
    args = p.parse_args()
    setup_logging()
    uvicorn.run("biflow.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
