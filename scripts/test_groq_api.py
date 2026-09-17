"""Opt-in live check using the application's Groq settings and Order schema."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import create_model_client, load_settings, structured_output
from app.graph.state import OrderSelection


def main() -> int:
    env_file = ROOT / ".env"
    try:
        settings = load_settings(env_file=env_file if env_file.is_file() else None)
        client = create_model_client(settings)
        selection = structured_output(client, OrderSelection).invoke(
            "Select the only pending order: smoke-order. Return order_id "
            "and a short explanation using the provided schema."
        )
        if not isinstance(selection, OrderSelection) or selection.order_id != "smoke-order":
            raise ValueError("Unexpected order selection")
    except Exception as exc:
        # Provider error bodies can contain request details; never print them.
        print(f"Groq smoke test failed ({type(exc).__name__}). Check configuration and connectivity.",
              file=sys.stderr)
        return 1
    print("Groq smoke test passed: OrderSelection structured output validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
