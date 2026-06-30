#!/usr/bin/env python3
"""Immutable AI Evaluator — separate process, read-only.

This process is loaded from a read-only filesystem mount and is never
in-scope for self-improvement. It evaluates candidate improvements
submitted by the L2 Improvement Engine.

Usage:
    echo '{"candidate": "..."}' | python evaluator.py

Startup:
    python evaluator.py --verify <expected_sha256>
"""

import hashlib
import json
import os
import sys


SYSTEM_PROMPT_FILE = os.path.join(os.path.dirname(__file__), "prompt.txt")


def load_system_prompt() -> str:
    """Load the immutable system prompt from the sidecar file."""
    with open(SYSTEM_PROMPT_FILE) as f:
        return f.read()


def self_verify(expected_digest: str) -> bool:
    """Verify our own binary digest at startup."""
    with open(__file__, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    ok = actual == expected_digest
    if not ok:
        print(json.dumps({
            "error": "DIGEST_MISMATCH",
            "expected": expected_digest[:16],
            "actual": actual[:16],
        }), file=sys.stderr)
    return ok


def evaluate(candidate: dict) -> dict:
    """Evaluate a candidate improvement using the AI model.

    In production this calls the configured AI API (GPT, Claude, etc.)
    with the system prompt. This stub demonstrates the interface.
    """
    system_prompt = load_system_prompt()

    # In production, replace with actual API call:
    # response = openai.chat.completions.create(
    #     model=os.environ.get("RSIS_EVALUATOR_MODEL", "gpt-4o-mini"),
    #     messages=[
    #         {"role": "system", "content": system_prompt},
    #         {"role": "user", "content": json.dumps(candidate)},
    #     ],
    # )
    # return json.loads(response.choices[0].message.content)

    # Stub — always returns PASS with perfect scores for testing
    return {
        "decision": "PASS",
        "scores": {
            "correctness": 1.0,
            "safety": 1.0,
            "efficiency": 1.0,
            "style": 1.0,
            "regression": 1.0,
        },
        "rationale": "Stub evaluator — all checks passed by default.",
        "suggestions": ["Replace stub with real AI caller in production."],
    }


def main() -> None:
    args = sys.argv[1:]

    # Optional self-verification on startup
    if "--verify" in args:
        idx = args.index("--verify")
        expected = args[idx + 1]
        if not self_verify(expected):
            sys.exit(1)

    # Read candidate from stdin
    try:
        raw = sys.stdin.read()
        candidate = json.loads(raw)
    except (json.JSONDecodeError, EOFError) as e:
        print(json.dumps({"error": f"INVALID_INPUT: {e}"}), file=sys.stderr)
        sys.exit(1)

    # Evaluate
    result = evaluate(candidate)

    # Output result
    print(json.dumps(result))


if __name__ == "__main__":
    main()
