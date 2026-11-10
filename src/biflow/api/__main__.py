"""Lance l'API (et le front compilé s'il existe) : python -m biflow.api [--port 8000] [--llm ollama]."""

from __future__ import annotations

import argparse
import os

import uvicorn

from biflow.common.logging import setup_logging


def main() -> None:
    p = argparse.ArgumentParser(description="API BIFlow + front React")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="rechargement auto (développement)")
    p.add_argument("--llm", choices=["auto", "mistral", "ollama", "none"],
                   help="fournisseur LLM (sinon BIFLOW_LLM_PROVIDER du .env)")
    args = p.parse_args()
    if args.llm:
        os.environ["BIFLOW_LLM_PROVIDER"] = args.llm  # lu par get_settings(), y compris avec --reload
    setup_logging()
    uvicorn.run("biflow.api.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
