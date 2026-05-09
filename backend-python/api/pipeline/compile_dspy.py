"""
DSPy compilation script — optimises the entertainment-analysis prompt using
BootstrapFewShot and uploads the compiled result to LangFuse.

Usage:
    python -m pipeline.compile_dspy

Requires data/examples.json with >= 50 labelled examples.
Outputs data/dspy_compiled.json and registers the compiled prompt in LangFuse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import dspy

# ── Paths ──────────────────────────────────────────────────────────────────

EXAMPLES_PATH = Path(os.getenv("EXAMPLES_PATH", "data/examples.json"))
OUTPUT_PATH = Path(os.getenv("DSPY_OUTPUT_PATH", "data/dspy_compiled.json"))

# ── DSPy Signature ─────────────────────────────────────────────────────────


class AnalyzeEntertainmentData(dspy.Signature):
    """Answer business questions about entertainment industry data."""

    question: str = dspy.InputField()
    sql_results: str = dspy.InputField()
    doc_context: str = dspy.InputField()
    answer: str = dspy.OutputField()


# ── Metric ─────────────────────────────────────────────────────────────────


def answer_match(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> bool:
    """
    Soft match: prediction is correct if it contains the key answer terms.
    Normalises case and strips punctuation for comparison.
    """
    pred = prediction.answer.lower().strip()
    gold = example.answer.lower().strip()

    # Accept if prediction contains at least 60% of the gold answer words
    gold_words = set(gold.split())
    if not gold_words:
        return False
    overlap = sum(1 for w in gold_words if w in pred)
    return overlap / len(gold_words) >= 0.6


# ── Main ───────────────────────────────────────────────────────────────────


def main() -> None:
    # 1. Load examples
    if not EXAMPLES_PATH.exists():
        raise FileNotFoundError(f"Examples not found at {EXAMPLES_PATH}")

    with EXAMPLES_PATH.open() as fh:
        raw = json.load(fh)

    assert len(raw) >= 50, (
        f"Need >= 50 examples, found {len(raw)}. "
        f"Add more entries to {EXAMPLES_PATH}"
    )

    # 2. Convert to DSPy examples
    all_examples = [
        dspy.Example(
            question=e["question"],
            sql_results=e["sql_results"],
            doc_context=e["doc_context"],
            answer=e["answer"],
        ).with_inputs("question", "sql_results", "doc_context")
        for e in raw
    ]

    train = all_examples[:40]
    validate = all_examples[40:50]

    print(f"Training set: {len(train)} examples")
    print(f"Validation set: {len(validate)} examples")

    # 3. Configure DSPy LM
    lm = dspy.LM(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        max_tokens=512,
    )
    dspy.configure(lm=lm)

    # 4. Compile with BootstrapFewShot
    teleprompter = dspy.BootstrapFewShot(
        metric=answer_match,
        max_bootstrapped_demos=8,
    )
    compiled = teleprompter.compile(
        dspy.ChainOfThought(AnalyzeEntertainmentData),
        trainset=train,
    )

    # 5. Export compiled prompt JSON
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    compiled_dict = compiled.dump_state()
    with OUTPUT_PATH.open("w") as fh:
        json.dump(compiled_dict, fh, indent=2)
    print(f"Compiled prompt saved to {OUTPUT_PATH}")

    # 6. Upload to LangFuse
    try:
        from observability.tracing import langfuse

        if langfuse is not None:
            langfuse.create_prompt(
                name="dspy-compiled",
                prompt=json.dumps(compiled_dict),
                labels=["production"],
                config={
                    "version": "2.0",
                    "examples": len(train),
                },
            )
            print("Compiled prompt registered in LangFuse as 'dspy-compiled' v2.0")
        else:
            print("LangFuse not configured — skipping prompt upload")
    except Exception as exc:
        print(f"Warning: LangFuse upload failed: {exc}")


if __name__ == "__main__":
    main()
