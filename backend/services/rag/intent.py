"""
Intent classification for RAG meeting intelligence queries.
Identifies decision-seeking queries, action item lookups, and general dialogue queries.
Strict constraints: No emojis, no em dashes.
"""

import re

# Patterns matching questions about decisions, action items, conclusions, or final agreements
DECISION_PATTERNS = [
    r"\b(last|latest|final|recent)\s+decision(s)?\b",
    r"\bwhat\s+(was|were|did\s+we)\s+(decide|decided|agree|agreed)\b",
    r"\b(did\s+we\s+decide|did\s+we\s+agree|we\s+decided)\b",
    r"\b(what\s+was\s+decided|what\s+was\s+the\s+final\s+decision)\b",
    r"\b(action\s+item|action\s+items|next\s+step|next\s+steps)\b",
    r"\bdecisions?\s+(made|taken|agreed|regarding|about)\b",
    r"\b(client\s+request|client\s+requests|proposal\s+was|proposal\s+to)\b",
    r"\b(accepted\s+or\s+rejected|was\s+accepted|was\s+rejected)\b",
    r"\b(who\s+is\s+assigned|task(s)?\s+assigned|pending\s+status|marked\s+as\s+pending)\b",
    r"\b(what\s+are\s+the\s+deliverables|immediate\s+next\s+steps)\b",
    r"\b(demo\s+scheduled|sync\s+scheduled|release\s+date)\b",
    r"\b(when\s+is\s+the\s+.*(scheduled|planned))\b",
    # Hindi and Hinglish patterns
    r"\b(faisla|faisle|kya\s+decide\s+(hua|hui|tha|thi)|decision\s+kya\s+tha|kya\s+final\s+hua)\b",
    r"\b(kisko\s+kya\s+karna\s+hai|action\s+kya\s+hai)\b",
    r"फैसला|निर्णय|क्या\s+तय\s+हुआ",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in DECISION_PATTERNS]

# Patterns matching consultative, advisory, brainstorming, or value-add questions
ADVISORY_PATTERNS = [
    r"\b(suggest|suggestion|suggestions|recommend|recommendation|recommendations)\b",
    r"\b(add\s+anything|anything\s+else|value\s+add|valuable|vaulable|add\s+more|something\s+else)\b",
    r"\b(improve|improvement|how\s+to\s+improve|better\s+way)\b",
    r"\b(missing|gaps?|risks?|critique|feedback)\b",
    r"\b(what\s+do\s+you\s+think|do\s+u\s+think|do\s+you\s+think|your\s+thoughts|your\s+opinion)\b",
    r"\b(scale|scaling|best\s+practices|future\s+scope|roadmap|enhancement|enhancements)\b",
    # Hindi and Hinglish patterns
    r"\b(kya\s+lagta\s+hai|kuch\s+aur\s+add|kya\s+behtar\s+kar\s+sakte|sujhaav)\b",
]

COMPILED_ADVISORY_PATTERNS = [re.compile(p, re.IGNORECASE) for p in ADVISORY_PATTERNS]


def is_decision_query(query: str) -> bool:
    """
    Classify whether a query is asking for decisions, action items, or final conclusions.
    """
    if not query:
        return False
    cleaned = query.strip()
    for pattern in COMPILED_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False


def is_advisory_query(query: str) -> bool:
    """
    Classify whether a query is asking for suggestions, feedback, critique, or value additions.
    """
    if not query:
        return False
    cleaned = query.strip()
    for pattern in COMPILED_ADVISORY_PATTERNS:
        if pattern.search(cleaned):
            return True
    return False


def classify_query_intent(query: str) -> str:
    """
    Returns classified intent label:
      - 'decision_lookup': Query specifically focuses on decisions, action items, or conclusions
      - 'general_qa': Broad meeting dialogue question
    """
    if is_decision_query(query):
        return "decision_lookup"
    return "general_qa"

