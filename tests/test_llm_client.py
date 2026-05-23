"""End-to-end smoke tests for llm.client.LLMClient.

Run from project root with venv active:
    python tests\\test_llm_client.py

Hits the live Ollama service; not a unit test. Exits 0 on full pass, 1 otherwise.
"""

import json
import sys
from pathlib import Path

# Allow running as a script from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.client import LLMClient  # noqa: E402


results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    print(f"  [{status}] {name}  {detail}")
    results.append((name, passed, detail))


def main() -> int:
    client = LLMClient()
    print(f"Using model={client.model} host={client.host}\n")

    # 1. generate — simple prompt
    print("Test 1: generate() with simple prompt")
    try:
        out = client.generate("Say the single word: hello.")
        print(f"    got: {out!r}")
        record("generate_simple", isinstance(out, str) and len(out.strip()) > 0,
               f"len={len(out)}")
    except Exception as e:
        record("generate_simple", False, f"exception: {e}")

    # 2. generate — with system prompt
    print("\nTest 2: generate() with system prompt")
    try:
        out = client.generate(
            prompt="What color is the sky on a clear day?",
            system="You must reply using only lowercase letters and no punctuation.",
        )
        print(f"    got: {out!r}")
        passed = out == out.lower() and not any(c in out for c in ".,!?;:")
        record("generate_system", passed, "followed lowercase+no-punct rule" if passed
               else "did not follow system instruction")
    except Exception as e:
        record("generate_system", False, f"exception: {e}")

    # 3. classify
    print("\nTest 3: classify() on job posting snippet")
    try:
        res = client.classify(
            text="We are hiring a senior backend engineer to build distributed systems in Go.",
            categories=["engineering", "marketing", "sales", "design"],
        )
        print(f"    got: {res}")
        passed = (res["category"] == "engineering"
                  and 0.0 <= res["confidence"] <= 1.0)
        record("classify", passed, f"category={res['category']} conf={res['confidence']}")
    except Exception as e:
        record("classify", False, f"exception: {e}")

    # 4. extract_json
    print("\nTest 4: extract_json() on job posting")
    try:
        sample = (
            "Staff Software Engineer at Acme Corp. Location: Remote (US). "
            "Posted 2026-04-10. Compensation competitive."
        )
        res = client.extract_json(
            text=sample,
            schema_description=(
                "Extract the job title, company name, location, and posting date. "
                "Return JSON with keys: title, company, location, posted_date."
            ),
        )
        print(f"    got: {json.dumps(res, indent=2)}")
        passed = isinstance(res, dict) and {"title", "company", "location", "posted_date"} <= set(res.keys())
        record("extract_json", passed, f"keys={sorted(res.keys())}")
    except Exception as e:
        record("extract_json", False, f"exception: {e}")

    # 5. score
    print("\nTest 5: score() on a resume-fit scenario")
    try:
        res = client.score(
            text="Candidate has 10 years of Python and distributed systems experience.",
            criteria="Fit for a senior backend engineer role requiring Python and distributed systems.",
            scale=(1, 10),
        )
        print(f"    got: {res}")
        passed = (isinstance(res["score"], int) and 1 <= res["score"] <= 10
                  and isinstance(res["reasoning"], str) and res["reasoning"])
        record("score", passed, f"score={res['score']}")
    except Exception as e:
        record("score", False, f"exception: {e}")

    # Summary
    print("\n" + "=" * 60)
    total = len(results)
    passed = sum(1 for _, p, _ in results if p)
    print(f"{passed}/{total} tests passed")
    for name, p, detail in results:
        print(f"  {'PASS' if p else 'FAIL'}: {name}  {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
