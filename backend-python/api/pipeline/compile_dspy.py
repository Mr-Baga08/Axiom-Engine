"""
DSPy compilation script — optimises the entertainment-analysis prompt using
BootstrapFewShot and saves the compiled few-shot demos to disk.

The compiled output is a JSON file containing the demonstrations that scored
highest against the validation set. At startup, main.py loads this file and
injects the demos into the agent's system prompt — zero extra API calls at
inference time.

Usage (run once after generating examples):
    python -m pipeline.compile_dspy

Environment variables:
    EXAMPLES_PATH       Path to examples JSON   (default: data/dspy_examples.json)
    DSPY_OUTPUT_PATH    Path to save compiled JSON (default: data/dspy_compiled.json)
    LLM_PROVIDER        anthropic | gemini       (default: anthropic)
    ANTHROPIC_API_KEY   Required when LLM_PROVIDER=anthropic
    GOOGLE_API_KEY      Required when LLM_PROVIDER=gemini

The script prints progress to stdout. Expect ~40–80 LM calls for 40 training
examples with max_bootstrapped_demos=4. On Gemini 2.5 Flash Lite this takes
roughly 5–15 minutes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import dspy

EXAMPLES_PATH   = Path(os.getenv("EXAMPLES_PATH",    "data/dspy_examples.json"))
OUTPUT_PATH     = Path(os.getenv("DSPY_OUTPUT_PATH", "data/dspy_compiled.json"))
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "anthropic").lower()


# ── DSPy signature ─────────────────────────────────────────────────────────────

class AnalyzeEntertainmentData(dspy.Signature):
    """
    You are an expert entertainment analytics assistant.
    Answer the business question accurately and concisely using the structured
    data results and any relevant document context provided.
    Always cite the source of your numbers.
    """

    question:    str = dspy.InputField(desc="The user's business question")
    sql_results: str = dspy.InputField(desc="JSON rows returned by the SQL query")
    doc_context: str = dspy.InputField(desc="Relevant text from PDF reports (may be empty)")
    answer:      str = dspy.OutputField(desc="A concise, accurate, sourced answer")


# ── Evaluation metric ─────────────────────────────────────────────────────────

def answer_quality(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> bool:
    """
    Soft word-overlap metric.
    Accepts the prediction if it contains >= 60% of the content words in the
    gold answer. This is intentionally lenient — the goal is to select demos
    that produce relevant, on-topic responses, not exact string matches.
    """
    pred  = prediction.answer.lower().strip()
    gold  = example.answer.lower().strip()

    # Strip common stop words so overlap reflects content, not filler
    stop  = {"the", "a", "an", "is", "are", "was", "were", "of", "in", "at",
              "to", "and", "or", "by", "for", "with", "has", "had"}
    words = {w for w in gold.split() if w not in stop and len(w) > 2}

    if not words:
        return True  # trivially accept empty gold answers

    overlap = sum(1 for w in words if w in pred)
    return overlap / len(words) >= 0.6


# ── LM configuration ─────────────────────────────────────────────────────────

def configure_lm() -> None:
    if LLM_PROVIDER == "gemini":
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")
        model_name = os.getenv("GEMINI_AGENT_MODEL", "gemini-2.5-flash-lite")
        lm = dspy.LM(
            model=f"gemini/{model_name}",
            api_key=api_key,
            max_tokens=512,
            num_retries=8,      # LiteLLM retries 429s with the delay the API requests
            cache=False,
        )
    else:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        model_name = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        lm = dspy.LM(
            model=f"anthropic/{model_name}",
            api_key=api_key,
            max_tokens=512,
            num_retries=8,
            cache=False,
        )

    dspy.configure(lm=lm)
    print(f"DSPy configured: provider={LLM_PROVIDER}, model={model_name}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    if not EXAMPLES_PATH.exists():
        raise FileNotFoundError(
            f"Examples not found at {EXAMPLES_PATH}. "
            f"Run: python scripts/generate_examples.py"
        )

    with EXAMPLES_PATH.open() as fh:
        raw = json.load(fh)

    if len(raw) < 20:
        raise ValueError(
            f"Need >= 20 examples for meaningful compilation, found {len(raw)}. "
            f"Re-run scripts/generate_examples.py against a populated database."
        )

    examples = [
        dspy.Example(
            question    = e["question"],
            sql_results = e["sql_results"],
            doc_context = e.get("doc_context", ""),
            answer      = e["answer"],
        ).with_inputs("question", "sql_results", "doc_context")
        for e in raw
    ]

    split = int(len(examples) * 0.8)
    train    = examples[:split]
    validate = examples[split:]

    print(f"Training: {len(train)} | Validation: {len(validate)}")

    configure_lm()

    teleprompter = dspy.BootstrapFewShot(
        metric=answer_quality,
        max_bootstrapped_demos=4,
        max_labeled_demos=4,
    )

    program = dspy.ChainOfThought(AnalyzeEntertainmentData)

    print("Starting BootstrapFewShot compilation...")
    compiled = teleprompter.compile(program, trainset=train)
    print("Compilation complete.")

    # Evaluate on validation set
    correct = sum(
        1 for ex in validate
        if answer_quality(ex, compiled(
            question=ex.question,
            sql_results=ex.sql_results,
            doc_context=ex.doc_context,
        ))
    )
    print(f"Validation accuracy: {correct}/{len(validate)} ({correct/len(validate)*100:.0f}%)")

    # Save compiled state
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = compiled.dump_state()
    with OUTPUT_PATH.open("w") as fh:
        json.dump(state, fh, indent=2)
    print(f"Compiled prompt saved to {OUTPUT_PATH}")

    # Upload to LangFuse if configured
    try:
        from observability.langfuse_client import get_client
        lf = get_client()
        create_fn = getattr(lf, "create_prompt", None) if lf else None
        if create_fn:
            create_fn(
                name="dspy-compiled",
                prompt=json.dumps(state),
                labels=["production"],
            )
            print("Compiled prompt registered in LangFuse as 'dspy-compiled'")
        else:
            print("LangFuse not configured — skipping prompt upload")
    except Exception as exc:
        print(f"LangFuse upload skipped: {exc}")


if __name__ == "__main__":
    main()
