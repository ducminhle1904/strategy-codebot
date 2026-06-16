"""Strategy Codebot CLI product."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

__all__ = ["__version__"]

try:
    __version__ = version("strategy-codebot")
except PackageNotFoundError:
    __version__ = "0.1.0"
