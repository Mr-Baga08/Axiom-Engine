"""
DSPy signatures and the compiled chain module.

The AnalyzeEntertainmentData signature takes structured tool outputs
(SQL results + document context) and synthesises a grounded final answer.

The compiled module (produced by compile.py) replaces raw LLM calls in
the agent's final synthesis step — it uses few-shot examples to produce
more accurate, consistently structured answers.
"""

from __future__ import annotations

import dspy


class AnalyzeEntertainmentData(dspy.Signature):
    """
    Synthesise a grounded answer from structured query results and document context.

    All three input fields are always provided. If sql_results or doc_context
    are empty, the model should acknowledge the absence and answer from what
    is available. The answer must be factual, cite sources where possible, and
    avoid hallucination.
    """

    question: str = dspy.InputField(
        desc="The original natural-language question from the user.",
    )
    sql_results: str = dspy.InputField(
        desc=(
            "JSON string of rows returned by the SQL tool. "
            "May be '[]' if no SQL tool was called."
        ),
    )
    doc_context: str = dspy.InputField(
        desc=(
            "Concatenated relevant document passages retrieved by CRAG, "
            "each prefixed with its source filename and page number. "
            "May be empty string if no documents were retrieved."
        ),
    )
    answer: str = dspy.OutputField(
        desc=(
            "A clear, factual answer to the question. "
            "Cite document sources as (source, p.N) inline. "
            "Cite SQL results as 'per structured data'. "
            "If uncertain, say so explicitly."
        ),
    )


# Module used by the agent — compiled in compile.py, loaded here at runtime
class EntertainmentAnalysisModule(dspy.Module):
    def __init__(self) -> None:
        super().__init__()
        self.analyse = dspy.ChainOfThought(AnalyzeEntertainmentData)

    def forward(self, question: str, sql_results: str, doc_context: str) -> dspy.Prediction:
        return self.analyse(
            question=question,
            sql_results=sql_results,
            doc_context=doc_context,
        )