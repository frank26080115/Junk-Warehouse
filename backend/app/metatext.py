from __future__ import annotations

import sys, argparse
import hashlib
import logging
import random
import uuid
import math
from collections import deque
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from sqlalchemy import MetaData, Table, select, text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine, Connection
from nltk.corpus import wordnet

from .db import get_engine, get_db_item_as_dict, get_or_create_session
from .helpers import deduplicate_preserving_order, split_words, normalize_pg_uuid, levenshtein_match
from ..automation.ai_helpers import EmbeddingAi

TAG_WORDS_TABLE_NAME = "tag_words"

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


def ensure_tag_words_table_exists(ai: EmbeddingAi = None, engine: Engine = None) -> str:
    if not ai:
        ai = EmbeddingAi()
    if not engine:
        engine = get_engine()
    inspector = inspect(engine)
    suffix = ai.get_as_suffix()
    table_name = f"{TAG_WORDS_TABLE_NAME}_{suffix}"
    if inspector.has_table(table_name, schema="public"):
        return table_name
    dimensions = ai.get_dimensions()
    if not isinstance(dimensions, int) or dimensions <= 0:
        raise RuntimeError("Embedding dimensions must be a positive integer before creating tables")

    statements = [
        f"""CREATE TABLE public.{table_name} (
    id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    word text UNIQUE NOT NULL,
    vec public.vector({dimensions}),
    date_updated timestamp with time zone DEFAULT now() NOT NULL
);""",
        f"CREATE INDEX idx_{table_name}_vec ON public.{table_name} USING hnsw (vec public.vector_cosine_ops) WITH (lists='100');",
        f"""CREATE FUNCTION public.touch_{table_name}_updated() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.date_updated := now();
    RETURN NEW;
END;
$$;""",
        f"CREATE TRIGGER trg_touch_{table_name}_updated BEFORE UPDATE ON public.{table_name} FOR EACH ROW EXECUTE FUNCTION public.touch_{table_name}_updated();",
    ]

    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)
    return table_name


def update_metatext(input: str, check_closest_n: int = 3) -> str:
    ai = EmbeddingAi()
    engine = get_engine()
    table_name = ensure_tag_words_table_exists(ai = ai, engine = engine)
    whitelist = # TODO split_words on appconfig.json key "meta_whitelist"
    words = deduplicate_preserving_order(split_words(input), lev_limit=1)
    results: list[str] = []
    new_inserts: list[str] = []
    for w in words:
        is_whitelisted = False
        # check if in whitelist, ignore word if it is
        for wlw in whitelist:
            if levenshtein_match(w, wlw):
                is_whitelisted = True
                results.append(wlw)
                break
        if is_whitelisted:
            continue
        needle_vec = ai.build_embedding_vector(w)[0]
        matches = # TODO, find the 3 (or check_closest_n) best matches (to needle_vec) from the table `table_name`, sorted best match first
        is_matched = False
        for m in matches:
            if levenshtein_match(w, m):
                results.append(wlw)
                is_matched = True
                break
        if is_matched:
            continue
        # nothing done? then keep the word
        results.append(w)
        new_inserts.append(w)

    new_inserts = deduplicate_preserving_order(new_inserts, lev_limit=1)
    for ni in new_inserts:
        vec = ai.build_embedding_vector(ni)[0]
        # TODO: insert into table `table_name`

    results = deduplicate_preserving_order(results, lev_limit=1)

    # TODO return results as a string, delimited by comma then space


def build_greedy_chain(word: str, limit: int = 50) -> list[dict]:
    ai = EmbeddingAi()
    engine = get_engine()
    table_name = ensure_tag_words_table_exists(ai = ai, engine = engine)

    ordered_result: list[dict] = []
    seen: set[str] = set()
    current_word = word
    current_vec = ai.build_embedding_vector(current_word)[0]
    while len(seen) < limit:
        matches = # TODO get top <limit> matches from table, sorted best match first
        has_added = False
        for mat in matches:
            next_word = mat["word"]
            # ignore something we've already seen
            if next_word in seen:
                continue
            seen.add(next_word)
            ordered_result.append(mat)
            current_word = next_word
            current_vec = mat["vec"]
            has_added = True
            break
        # if nothing has been added to the list, it could mean we've seen all the words but have not hit the limit
        # so we are done
        if not has_added:
            break
    return ordered_result


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
