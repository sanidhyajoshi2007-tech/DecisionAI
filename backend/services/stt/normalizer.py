"""
Text normalization and Word Error Rate (WER) utilities for Hindi (Devanagari)
and conversational Hinglish evaluation.
"""

from dataclasses import dataclass
import re
import unicodedata


# Devanagari Nukta character mapping to base characters
NUKTA_MAP = {
    "\u0958": "\u0915",  # क़ -> क
    "\u0959": "\u0916",  # ख़ -> ख
    "\u095a": "\u0917",  # ग़ -> ग
    "\u095b": "\u091c",  # ज़ -> ज
    "\u095c": "\u0921",  # ड़ -> ड
    "\u095d": "\u0922",  # ढ़ -> ढ
    "\u095e": "\u092b",  # फ़ -> फ
    "\u095f": "\u092f",  # य़ -> य
}

# Common Hinglish phonetic spelling variations normalized to canonical forms
HINGLISH_CANONICAL_MAP = {
    "bhee": "bhi",
    "bhe": "bhi",
    "nahin": "nahi",
    "nahee": "nahi",
    "nahii": "nahi",
    "hain": "hai",
    "hein": "hai",
    "h": "hai",
    "krna": "karna",
    "krenge": "karenge",
    "karengy": "karenge",
    "krte": "karte",
    "karne": "karne",
    "krne": "karne",
    "chahiye": "chahiye",
    "chaahiye": "chahiye",
    "shuru": "shuru",
    "suru": "shuru",
    "plz": "please",
    "pls": "please",
    "thnx": "thanks",
    "thx": "thanks",
    "kyu": "kyun",
    "kyon": "kyun",
    "kyoo": "kyun",
    "aaj": "aaj",
    "kal": "kal",
    "meeting": "meeting",
    "call": "call",
    "standup": "standup",
}


def normalize_hindi(text: str, remove_nukta: bool = True) -> str:
    """
    Normalize Hindi Devanagari text:
    - Unicode NFC normalization
    - Strips zero-width characters (ZWJ, ZWNJ, ZWSP)
    - Normalizes or strips Nukta characters
    - Strips Hindi danda (।), double danda (॥), and standard punctuation
    - Collapses multiple whitespace
    """
    if not text:
        return ""

    # 1. Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)

    # 2. Strip zero-width characters (ZWJ, ZWNJ, BOM, etc.)
    text = re.sub(r"[\u200B-\u200D\uFEFF]", "", text)

    # 3. Nukta normalization
    if remove_nukta:
        for nukta_char, base_char in NUKTA_MAP.items():
            text = text.replace(nukta_char, base_char)
        # Also remove combining nukta character '\u093C'
        text = text.replace("\u093C", "")

    # 4. Strip punctuation including Hindi dandas (। \u0964, ॥ \u0965)
    text = re.sub(r"[\u0964\u0965\.,!?;:\'\"`\(\)\[\]\{\}\-_/\\<>@#$%^&*+=~|]", " ", text)

    # 5. Collapse spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_hinglish(text: str) -> str:
    """
    Normalize Hinglish (conversational code-switched Hindi-English) text:
    - Lowercase conversion
    - Normalize Hindi Devanagari components
    - Normalize common romanized Hindi phonetic spelling variations
    - Strip Latin and Indic punctuation
    - Clean up repeated letters (e.g. "sooooo" -> "so")
    """
    if not text:
        return ""

    # 1. First apply Hindi normalization (cleans dandas, nuktas, zero-width chars)
    text = normalize_hindi(text, remove_nukta=True)

    # 2. Lowercase
    text = text.lower()

    # 3. Normalize repeated vowels / consonants (>2 repetitions -> 1)
    text = re.sub(r"([a-z])\1{2,}", r"\1", text)

    # 4. Token-level canonical mapping for common Hinglish variants
    tokens = text.split()
    normalized_tokens = [HINGLISH_CANONICAL_MAP.get(tok, tok) for tok in tokens]

    return " ".join(normalized_tokens)


@dataclass
class WERResult:
    """Word Error Rate calculation breakdown."""

    wer: float
    substitutions: int
    deletions: int
    insertions: int
    hits: int
    ref_words: int
    hyp_words: int

    @property
    def wer_percent(self) -> float:
        return round(self.wer * 100.0, 2)


def compute_word_error_rate(
    reference: str,
    hypothesis: str,
    mode: str = "general",
) -> WERResult:
    """
    Compute Word Error Rate (WER) using dynamic programming Levenshtein distance.

    Args:
        reference: Ground truth human-corrected text.
        hypothesis: Model-generated transcription text.
        mode: Normalization mode:
            - 'hindi': Normalizes Devanagari Hindi text.
            - 'hinglish': Normalizes mixed Hindi-English words.
            - 'general': Basic lowercase & punctuation stripping.
            - 'raw': No normalization.

    Returns:
        WERResult with WER score and S, D, I, H counts.
    """
    if mode == "hindi":
        ref_clean = normalize_hindi(reference)
        hyp_clean = normalize_hindi(hypothesis)
    elif mode == "hinglish":
        ref_clean = normalize_hinglish(reference)
        hyp_clean = normalize_hinglish(hypothesis)
    elif mode == "general":
        ref_clean = re.sub(r"[^\w\s]", "", reference.lower()).strip()
        hyp_clean = re.sub(r"[^\w\s]", "", hypothesis.lower()).strip()
    else:
        ref_clean = reference.strip()
        hyp_clean = hypothesis.strip()

    r = ref_clean.split()
    h = hyp_clean.split()

    n = len(r)
    m = len(h)

    if n == 0:
        if m == 0:
            return WERResult(wer=0.0, substitutions=0, deletions=0, insertions=0, hits=0, ref_words=0, hyp_words=0)
        return WERResult(wer=1.0, substitutions=0, deletions=0, insertions=m, hits=0, ref_words=0, hyp_words=m)

    # Dynamic programming distance matrix
    # dp[i][j] = (distance, substitutions, deletions, insertions)
    dp = [[(0, 0, 0, 0) for _ in range(m + 1)] for _ in range(n + 1)]

    for i in range(1, n + 1):
        dp[i][0] = (i, 0, i, 0)  # Deletions

    for j in range(1, m + 1):
        dp[0][j] = (j, 0, 0, j)  # Insertions

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if r[i - 1] == h[j - 1]:
                dp[i][j] = dp[i - 1][j - 1]
            else:
                # Substitution
                sub_cost = dp[i - 1][j - 1][0] + 1
                # Deletion
                del_cost = dp[i - 1][j][0] + 1
                # Insertion
                ins_cost = dp[i][j - 1][0] + 1

                min_cost = min(sub_cost, del_cost, ins_cost)
                if min_cost == sub_cost:
                    cost, s, d, ins = dp[i - 1][j - 1]
                    dp[i][j] = (sub_cost, s + 1, d, ins)
                elif min_cost == del_cost:
                    cost, s, d, ins = dp[i - 1][j]
                    dp[i][j] = (del_cost, s, d + 1, ins)
                else:
                    cost, s, d, ins = dp[i][j - 1]
                    dp[i][j] = (ins_cost, s, d, ins + 1)

    total_dist, subs, dels, inss = dp[n][m]
    hits = n - subs - dels
    wer = total_dist / float(n)

    return WERResult(
        wer=wer,
        substitutions=subs,
        deletions=dels,
        insertions=inss,
        hits=hits,
        ref_words=n,
        hyp_words=m,
    )
