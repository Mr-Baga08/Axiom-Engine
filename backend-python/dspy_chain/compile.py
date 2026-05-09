"""
DSPy compilation script.

Loads 20 labelled examples from data/examples.json, compiles the
EntertainmentAnalysisModule with BootstrapFewShot, and saves the
compiled module to data/dspy_compiled.json.

Run this script once after data/examples.json is populated:
    python -m python.dspy_chain.compile

The agent loads the compiled module at startup. If the compiled file
does not exist, the agent falls back to the uncompiled module.

data/examples.json format (array of 20 objects):
[
  {
    "question": "Which genre had the highest revenue in 2025?",
    "sql_results": "[{\"genre\": \"Action\", \"total_revenue\": 1200000000}]",
    "doc_context": "",
    "answer": "Action had the highest revenue in 2025 at $1.2B (per structured data)."
  },
  ...
]
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import dspy
from dspy.teleprompt import BootstrapFewShot

from .signatures import EntertainmentAnalysisModule

logger = logging.getLogger(__name__)

EXAMPLES_PATH = Path(os.getenv("DSPY_EXAMPLES_PATH", "data/examples.json"))
COMPILED_PATH = Path("data/dspy_compiled.json")


def _load_examples() -> list[dspy.Example]:
    if not EXAMPLES_PATH.exists():
        raise FileNotFoundError(
            f"DSPy examples file not found: {EXAMPLES_PATH}. "
            "Create it with 20 labelled examples before running compile.py."
        )
    with EXAMPLES_PATH.open() as fh:
        raw = json.load(fh)

    if len(raw) < 20:
        raise ValueError(
            f"Expected at least 20 examples in {EXAMPLES_PATH}, got {len(raw)}."
        )

    return [
        dspy.Example(
            question=e["question"],
            sql_results=e["sql_results"],
            doc_context=e["doc_context"],
            answer=e["answer"],
        ).with_inputs("question", "sql_results", "doc_context")
        for e in raw
    ]


def _answer_metric(example: dspy.Example, prediction: dspy.Prediction, trace=None) -> float:
    """
    Simple non-empty answer metric for BootstrapFewShot.
    A real metric would compare against reference answers semantically.
    Replace with a proper metric once an evaluation set is available.
    """
    return float(bool(prediction.answer and len(prediction.answer.strip()) > 10))


def compile_module() -> None:
    # Configure DSPy to use the same OpenAI model as the evaluator
    model_name = os.getenv("EVAL_LLM_MODEL", "gpt-4o-mini")
    dspy.configure(lm=dspy.OpenAI(model=model_name, max_tokens=1024))

    examples = _load_examples()
    module = EntertainmentAnalysisModule()

    teleprompter = BootstrapFewShot(
        metric=_answer_metric,
        max_bootstrapped_demos=4,
        max_labeled_demos=4,
    )

    compiled = teleprompter.compile(module, trainset=examples)
    compiled.save(str(COMPILED_PATH))
    logger.info("Compiled DSPy module saved to %s", COMPILED_PATH)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    compile_module()