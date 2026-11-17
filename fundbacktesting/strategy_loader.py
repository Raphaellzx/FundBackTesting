from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from .contracts import Strategy


def load_strategy(path: str | Path) -> tuple[Strategy, str]:
    """Load a strategy module exposing create_strategy() from a Python file."""
    strategy_path = Path(path).expanduser().resolve()
    if not strategy_path.is_file():
        raise FileNotFoundError(f"Strategy file does not exist: {strategy_path}")
    if strategy_path.suffix.lower() != ".py":
        raise ValueError(f"Strategy file must be a Python file: {strategy_path}")

    module_name = f"_fundbacktesting_strategy_{strategy_path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, strategy_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load strategy module from {strategy_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise

    factory = getattr(module, "create_strategy", None)
    if not callable(factory):
        sys.modules.pop(module_name, None)
        raise ValueError(
            f"Strategy module {strategy_path} must define create_strategy()."
        )

    strategy = factory()
    if not callable(getattr(strategy, "decide", None)):
        sys.modules.pop(module_name, None)
        raise TypeError(
            f"create_strategy() in {strategy_path} must return an object "
            "with a callable decide(context) method."
        )

    strategy_name = getattr(module, "STRATEGY_NAME", strategy_path.stem)
    if not isinstance(strategy_name, str) or not re.fullmatch(
        r"[\w-]+", strategy_name, flags=re.UNICODE
    ):
        sys.modules.pop(module_name, None)
        raise ValueError(
            "STRATEGY_NAME must contain only letters, numbers, underscores, "
            "or hyphens."
        )
    return strategy, strategy_name
