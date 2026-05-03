"""Tool definitions registered with the Anthropic API.

The tool description and parameter docs are themselves prompts — they steer
the model's behavior at every call site. Designed deliberately:
  - Search-by-default framing so the model uses Wikipedia for any verifiable
    question rather than answering from priors.
  - Explicit exceptions (arithmetic, code, opinion) so the model has a clean
    non-search rule for negative-capability cases.
  - Decomposition guidance for multi-part / synthesis questions.
  - Strong re-search guidance: assess results, refine, and admit when the
    information is genuinely not in Wikipedia rather than guessing.
"""

from __future__ import annotations

from typing import Any, Final

SEARCH_WIKIPEDIA_TOOL: Final[dict[str, Any]] = {
    "name": "search_wikipedia",
    "description": (
        "Search English Wikipedia and return the top results with extracts.\n\n"
        "Use this tool by default for any user question that benefits from "
        "being grounded in or verified against an external source. The only "
        "exceptions are questions Wikipedia cannot reasonably answer: "
        "arithmetic and calculations, code generation, opinion or preference "
        "questions, and questions about content already provided in the "
        "conversation. When in doubt, search.\n\n"
        "For multi-part or synthesis questions, decompose into sub-searches "
        "(one per facet) and combine the findings.\n\n"
        "Returns up to 3 results, each with title, URL, and a plain-text "
        "extract from the article's lead section (truncated to ~2000 "
        "characters; the response indicates when truncation occurred).\n\n"
        "After receiving results, assess whether they contain what is needed. "
        "If not, search again with a refined query, or with a different "
        "component of a multi-part question. If after several searches the "
        "information is genuinely not available in Wikipedia, say so rather "
        "than guessing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A search query targeting the article(s) most likely to "
                    "contain the needed information. Favor specific noun "
                    "phrases over conversational questions: 'Treaty of "
                    "Versailles signatories' rather than 'who signed the "
                    "Treaty of Versailles?'. For multi-part questions, issue "
                    "separate calls — each query targeting one facet."
                ),
            }
        },
        "required": ["query"],
    },
}
