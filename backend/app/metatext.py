from __future__ import annotations

import sys, argparse

from collections import deque

from nltk.corpus import wordnet

from .helpers import deduplicate_preserving_order, split_words

"""Utility helpers that expand words into synonym and variant lists."""

# Curated list of common American to British spelling shifts. The inverse mapping is
# derived programmatically so both conversion directions are covered.
AMERICAN_TO_BRITISH_SPELLINGS: dict[str, str] = {
    "analyze": "analyse",
    "apologize": "apologise",
    "center": "centre",
    "color": "colour",
    "defense": "defence",
    "dialog": "dialogue",
    "favorite": "favourite",
    "honor": "honour",
    "liter": "litre",
    "organize": "organise",
    "program": "programme",
    "realize": "realise",
    "rumor": "rumour",
    "theater": "theatre",
    "traveler": "traveller",
}

BRITISH_TO_AMERICAN_SPELLINGS: dict[str, str] = {
    british: american for american, british in AMERICAN_TO_BRITISH_SPELLINGS.items()
}

# Small collection of irregular plural forms so that pluralization can emit both
# the irregular plural and its singular counterpart when traversing recursively.
IRREGULAR_PLURALS: dict[str, str] = {
    "child": "children",
    "foot": "feet",
    "goose": "geese",
    "man": "men",
    "mouse": "mice",
    "person": "people",
    "tooth": "teeth",
    "woman": "women",
}

IRREGULAR_SINGULARS: dict[str, str] = {
    plural: singular for singular, plural in IRREGULAR_PLURALS.items()
}

# Suffixes where inserting a hyphen before the suffix often produces a legitimate
# textual variant, especially when a compound word is being emphasised.
HYPHENATABLE_SUFFIXES: tuple[str, ...] = (
    "able",
    "ible",
    "ing",
    "ed",
    "er",
    "est",
    "ful",
    "less",
    "like",
    "ling",
    "ly",
    "ness",
    "ship",
)


def _normalize_candidate(text: str) -> set[str]:
    """Return a set of clean variants produced from raw lemma text."""

    stripped = text.strip()
    if not stripped:
        return set()

    variants: set[str] = {stripped}
    collapsed = stripped.replace(" ", "").replace("-", "")
    hyphenated = stripped.replace(" ", "-")

    variants.add(collapsed)
    variants.add(hyphenated)
    variants.add(stripped.replace(" ", ""))

    return {variant.lower() for variant in variants if variant}


def _generate_wordnet_candidates(word: str) -> set[str]:
    """Gather synonyms and alternative lemmas from WordNet."""

    candidates: set[str] = set()

    try:
        synsets = wordnet.synsets(word)
    except LookupError as ex:
        print(f"LookupError {ex!r}")
        # When the WordNet corpus is unavailable the helper gracefully
        # degrades to morphological transformations only.
        return candidates

    for synset in synsets:
        for lemma in synset.lemmas():
            raw_name = lemma.name().replace("_", " ")
            candidates.update(_normalize_candidate(raw_name))

    return candidates


def _generate_plural_candidates(word: str) -> set[str]:
    """Return plausible pluralisations and singular forms."""

    lower_word = word.lower()
    candidates: set[str] = set()

    if lower_word in IRREGULAR_PLURALS:
        candidates.add(IRREGULAR_PLURALS[lower_word])
    if lower_word in IRREGULAR_SINGULARS:
        candidates.add(IRREGULAR_SINGULARS[lower_word])

    if lower_word.endswith("y") and len(lower_word) > 2 and lower_word[-2] not in "aeiou":
        candidates.add(f"{lower_word[:-1]}ies")
    elif lower_word.endswith(("s", "x", "z", "ch", "sh")):
        candidates.add(f"{lower_word}es")
    else:
        candidates.add(f"{lower_word}s")

    # Include a simple singular heuristic so plural inputs return a singular option.
    if lower_word.endswith("ies") and len(lower_word) > 3:
        candidates.add(f"{lower_word[:-3]}y")
    elif lower_word.endswith("es") and len(lower_word) > 2:
        candidates.add(lower_word[:-2])
    elif lower_word.endswith("s") and len(lower_word) > 1:
        candidates.add(lower_word[:-1])

    return candidates


def _generate_british_variants(word: str) -> set[str]:
    """Return American and British spellings for the supplied word."""

    lower_word = word.lower()
    candidates: set[str] = set()

    if lower_word in AMERICAN_TO_BRITISH_SPELLINGS:
        candidates.add(AMERICAN_TO_BRITISH_SPELLINGS[lower_word])
    if lower_word in BRITISH_TO_AMERICAN_SPELLINGS:
        candidates.add(BRITISH_TO_AMERICAN_SPELLINGS[lower_word])

    return candidates


def _generate_hyphenated_variants(word: str) -> set[str]:
    """Create hyphenated and de-hyphenated variants for the supplied word."""

    lower_word = word.lower()
    candidates: set[str] = set()

    if "-" in lower_word:
        candidates.add(lower_word.replace("-", ""))
    else:
        for suffix in HYPHENATABLE_SUFFIXES:
            if lower_word.endswith(suffix) and len(lower_word) > len(suffix) + 1:
                prefix = lower_word[: -len(suffix)]
                candidates.add(f"{prefix}-{suffix}")

    return candidates


def _generate_desuffixed_variants(word: str) -> set[str]:
    """Create hyphenated and de-hyphenated variants for the supplied word."""

    lower_word = word.lower()
    candidates: set[str] = set()

    if "-" in lower_word:
        candidates.add(lower_word.replace("-", ""))
    for suffix in HYPHENATABLE_SUFFIXES:
        if lower_word.endswith(suffix) and len(lower_word) > len(suffix) + 1:
            prefix = lower_word[: -len(suffix)]
            candidates.add(prefix)
            if prefix.endswith('s'):
                prefix = prefix[0:-1]
                candidates.add(prefix)

    return candidates


def _collect_direct_variants(word: str) -> set[str]:
    """Collect one-hop variants that feed the recursive synonym expansion."""

    variants: set[str] = set()
    variants.update(_generate_wordnet_candidates(word))
    #variants.update(_generate_plural_candidates(word))
    variants.update(_generate_british_variants(word))
    #variants.update(_generate_hyphenated_variants(word))
    variants.update(_generate_desuffixed_variants(word))

    return {variant for variant in variants if variant}


def get_word_synonyms(word: str) -> list[str]:
    """Return a recursively expanded, de-duplicated list of word variants."""

    cleaned = word.strip()
    if not cleaned:
        return []

    synonyms: set[str] = set()
    variants: set[str] = set()
    synonyms.add(word)
    synonyms.update(_generate_wordnet_candidates(word))
    variants.update(synonyms)
    variants.update(_generate_plural_candidates(word))
    variants.update(_generate_british_variants(word))
    variants.update(_generate_hyphenated_variants(word))
    variants.update(_generate_desuffixed_variants(word))
    for s in synonyms:
        variants.update(_generate_plural_candidates(s))
        variants.update(_generate_british_variants(s))
        variants.update(_generate_hyphenated_variants(s))
        variants.update(_generate_desuffixed_variants(s))
    ordered_results: list[str] = deduplicate_preserving_order(list(variants))
    for i in range(len(ordered_results)):
        ordered_results[i] = ordered_results[i].replace(' ', '-')

    return deduplicate_preserving_order(ordered_results)


def get_synonyms_for_words(words: Union[list[str], str]) -> list[str]:
    """Expand each word in ``words`` and return a combined de-duplicated list."""

    if isinstance(words, str):
        words = split_words(words)

    expanded: list[str] = []
    for word in words:
        # Reuse the single-word helper so behaviour stays perfectly aligned.
        expanded.extend(get_word_synonyms(word))

    return deduplicate_preserving_order(expanded)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test the word synonym exploder")
    parser.add_argument("string")
    args = parser.parse_args()
    lst = split_words(args.string)
    for i in lst:
        print(f"WORD: {i}")
        s = get_word_synonyms(i)
        for j in s:
            print(f"\t{j}")
