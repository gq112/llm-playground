"""Command-line launcher for vLLM and SGLang Observability."""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the vLLM and SGLang observability dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", "-p", type=int, default=7860, help="Port to bind (default: 7860)")
    parser.add_argument("--reload", "-r", action="store_true", help="Enable development auto-reload")
    args = parser.parse_args()
    from .app import main as run
    run(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
