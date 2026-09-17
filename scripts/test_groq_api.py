"""Test script to verify Groq Cloud API connectivity and model execution.

Usage:
    python scripts/test_groq_api.py
    python scripts/test_groq_api.py --list-models
    python scripts/test_groq_api.py --model qwen/qwen3.8-27b
    python scripts/test_groq_api.py --key gsk_...
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# Load from .env if present
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass


def parse_args():
    parser = argparse.ArgumentParser(description="Test Groq Cloud API key and models.")
    parser.add_argument(
        "--model",
        default=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
        help="Groq model ID (default: qwen/qwen3.8-27b or from GROQ_MODEL)",
    )
    parser.add_argument(
        "--key",
        default=os.getenv("GROQ_API_KEY"),
        help="Groq API key (default: read from GROQ_API_KEY in environment or .env)",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="List all active models available on your Groq Cloud account",
    )
    return parser.parse_args()


def list_available_models(api_key: str):
    from groq import Groq
    client = Groq(api_key=api_key)
    models = sorted([m.id for m in client.models.list().data])
    print("=" * 60)
    print("  Active Models on your Groq Cloud Account")
    print("=" * 60)
    for m in models:
        print(f"  - {m}")
    print("=" * 60)


def extract_json(text: str) -> dict:
    """Extract and parse JSON, handling potential markdown code fences."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    return json.loads(text)


def test_groq_connection(api_key: str, model: str):
    from groq import Groq

    print("=" * 60)
    print("  Groq Cloud API Key & Model Connectivity Test")
    print("=" * 60)
    print(f"Model ID: {model}")
    masked_key = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
    print(f"API Key:  {masked_key}")
    print("-" * 60)

    client = Groq(api_key=api_key)

    # 1. First test: Basic Connectivity
    print("\n[Step 1/2] Testing standard chat completion...")
    start_time = time.time()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a concise assistant."},
                {"role": "user", "content": "Respond with 'Groq API is working!' and today's date."},
            ],
            max_tokens=100,
        )
        elapsed = time.time() - start_time
        reply_text = response.choices[0].message.content or ""
        reply_text = reply_text.strip()
        usage = response.usage
        print(f"-> Success! Latency: {elapsed:.2f}s")
        print(f"-> Response: \"{reply_text}\"")
        if usage:
            print(f"-> Tokens used: prompt={usage.prompt_tokens}, completion={usage.completion_tokens}, total={usage.total_tokens}")
    except Exception as exc:
        print(f"\n[FAIL] Chat completion failed: {exc}")
        return False

    # 2. Second test: JSON Structured Output (needed for Order Agent)
    print("\n[Step 2/2] Testing JSON structured output for Warehouse Order selection...")
    start_time = time.time()
    try:
        sample_prompt = (
            "Select one pending order from: [{'id': 'ORD-101', 'item_count': 2}, {'id': 'ORD-102', 'item_count': 1}]. "
            "Return valid JSON only with keys 'order_id' and 'explanation'."
        )
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a warehouse coordinator. Output valid JSON only with keys 'order_id' and 'explanation'."},
                {"role": "user", "content": sample_prompt},
            ],
            temperature=0.0,
            max_tokens=150,
        )
        elapsed = time.time() - start_time
        raw_text = response.choices[0].message.content or ""
        parsed = extract_json(raw_text)
        print(f"-> Success! Latency: {elapsed:.2f}s")
        print(f"-> Structured JSON Output: {json.dumps(parsed, indent=2)}")
        assert "order_id" in parsed, "Missing 'order_id' key in response"

    except Exception as exc:
        print(f"\n[FAIL] Structured output test failed: {exc}")
        return False

    print("\n" + "=" * 60)
    print("  All Groq Cloud API tests passed successfully!")
    print("=" * 60)
    return True


def main():
    args = parse_args()
    if not args.key:
        print("[ERROR] No Groq API key found.")
        print("Please provide it via:")
        print("  1. Environment variable GROQ_API_KEY")
        print("  2. Inside a .env file: GROQ_API_KEY=gsk_...")
        print("  3. Pass as argument: python scripts/test_groq_api.py --key gsk_...")
        sys.exit(1)

    if args.list_models:
        list_available_models(api_key=args.key)
        sys.exit(0)

    success = test_groq_connection(api_key=args.key, model=args.model)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
