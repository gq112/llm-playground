"""
vLLM Observability - a focused dashboard for vLLM Prometheus metrics
"""

__version__ = "0.1.8"
__author__ = "micytao"
__description__ = "A focused observability dashboard for vLLM Prometheus metrics"

from pathlib import Path

# Package root directory (where this __init__.py lives)
PACKAGE_DIR = Path(__file__).parent


# Expose main function for programmatic use
def run(host: str = "0.0.0.0", port: int = 7860, reload: bool = False):
    """
    Run the vLLM observability dashboard.

    Args:
        host: Host to bind to (default: 0.0.0.0)
        port: Port to listen on (default: 7860)
        reload: Enable auto-reload for development (default: False)
    """
    from .app import main as _main

    _main(host=host, port=port, reload=reload)


__all__ = ["run", "__version__", "PACKAGE_DIR"]
