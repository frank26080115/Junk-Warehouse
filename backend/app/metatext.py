from __future__ import annotations

import sys, argparse
import hashlib
import logging
import random
import uuid
import math
from collections import deque
from typing import Any, Mapping, Optional, Sequence, Union

from flask import Blueprint, jsonify, request
from sqlalchemy import text, inspect
from sqlalchemy.orm import Session
from sqlalchemy.engine import Engine, Connection
from nltk.corpus import wordnet

from .db import get_engine, get_db_item_as_dict, get_or_create_session
from .helpers import deduplicate_preserving_order, split_words, normalize_pg_uuid, levenshtein_match
from .config_loader import load_app_config
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


def _coerce_vector_to_list(raw_vector: Sequence[float] | Any) -> list[float]:
    """Return ``raw_vector`` as a plain list of floats."""

    if raw_vector is None:
        return []

    candidate = raw_vector
    if hasattr(candidate, "tolist"):
        candidate = candidate.tolist()

    try:
        return [float(value) for value in list(candidate)]
    except (TypeError, ValueError):
        try:
            return list(candidate)
        except TypeError:
            return []


def _fetch_nearest_tag_words(
    conn: Connection,
    table_name: str,
    vector: Sequence[float],
    limit: int,
) -> list[Mapping[str, Any]]:
    """Return the ``limit`` closest tag words for ``vector``."""

    limit_value = max(int(limit), 1)
    vector_list = _coerce_vector_to_list(vector)
    if not vector_list:
        return []

    sql = text(
        f"""
SELECT word, vec, (vec <=> :needle_vec) AS embedding_distance
FROM public.{table_name}
WHERE vec IS NOT NULL
ORDER BY embedding_distance ASC
LIMIT :limit
"""
    )

    rows = conn.execute(
        sql,
        {"needle_vec": vector_list, "limit": limit_value},
    ).mappings().all()

    return list(rows)


def _load_whitelist_words() -> list[str]:
    """Return the configured whitelist of meta text terms."""

    cfg = load_app_config()
    if not isinstance(cfg, Mapping):
        return []

    raw_whitelist = cfg.get("meta_whitelist", "")
    extracted: list[str] = []

    if isinstance(raw_whitelist, str):
        extracted.extend(split_words(raw_whitelist))
    elif isinstance(raw_whitelist, Sequence):
        for entry in raw_whitelist:
            extracted.extend(split_words(str(entry)))
    elif raw_whitelist is not None:
        extracted.extend(split_words(str(raw_whitelist)))

    return deduplicate_preserving_order(extracted, lev_limit=1)


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
    table_name = ensure_tag_words_table_exists(ai=ai, engine=engine)
    whitelist = _load_whitelist_words()
    words = deduplicate_preserving_order(split_words(input), lev_limit=1)
    results: list[str] = []
    new_inserts: list[str] = []
    vector_cache: dict[str, list[float]] = {}

    with engine.begin() as conn:
        for word in words:
            whitelist_match = next(
                (candidate for candidate in whitelist if levenshtein_match(word, candidate)),
                None,
            )
            if whitelist_match:
                results.append(whitelist_match)
                continue

            needle_vec = ai.build_embedding_vector(word)[0]
            vector_cache[word] = _coerce_vector_to_list(needle_vec)
            matches = _fetch_nearest_tag_words(conn, table_name, needle_vec, check_closest_n)

            matched_entry: Optional[Mapping[str, Any]] = None
            for candidate in matches:
                candidate_word = candidate.get("word")
                if isinstance(candidate_word, str) and levenshtein_match(word, candidate_word):
                    matched_entry = candidate
                    break

            if matched_entry:
                results.append(str(matched_entry["word"]))
                continue

            results.append(word)
            new_inserts.append(word)

        new_inserts = deduplicate_preserving_order(new_inserts, lev_limit=1)
        for word in new_inserts:
            vector = vector_cache.get(word) or _coerce_vector_to_list(ai.build_embedding_vector(word)[0])
            if not vector:
                continue

            conn.execute(
                text(
                    f"""
INSERT INTO public.{table_name} (word, vec)
VALUES (:word, :vec)
ON CONFLICT (word) DO UPDATE
SET vec = EXCLUDED.vec,
    date_updated = now();
"""
                ),
                {"word": word, "vec": vector},
            )

    results = deduplicate_preserving_order(results, lev_limit=1)

    return ", ".join(results)


def build_greedy_chain(word: str, limit: int = 50) -> list[dict]:
    ai = EmbeddingAi()
    engine = get_engine()
    table_name = ensure_tag_words_table_exists(ai=ai, engine=engine)

    if limit <= 0:
        return []

    ordered_result: list[dict] = []
    seen: set[str] = {word}
    current_vec = ai.build_embedding_vector(word)[0]

    with engine.begin() as conn:
        while len(ordered_result) < limit:
            matches = _fetch_nearest_tag_words(conn, table_name, current_vec, limit)
            has_added = False
            for candidate in matches:
                next_word = candidate.get("word")
                if not isinstance(next_word, str) or next_word in seen:
                    continue

                vec_value = _coerce_vector_to_list(candidate.get("vec"))
                result_entry = {
                    "word": next_word,
                    "vec": vec_value,
                    "embedding_distance": candidate.get("embedding_distance"),
                }
                ordered_result.append(result_entry)
                seen.add(next_word)
                if vec_value:
                    current_vec = vec_value
                has_added = True
                break

            if not has_added:
                break

    return ordered_result


bp = Blueprint("metatext", __name__, url_prefix="/api")
PAGE_SIZE = 100


def _serialize_tag_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a raw database row into a JSON-friendly dictionary."""

    serialized: dict[str, Any] = dict(row)
    identifier = serialized.get("id")
    if identifier is not None:
        try:
            serialized["id"] = str(identifier)
        except Exception:
            serialized["id"] = identifier

    vec_value = serialized.get("vec")
    serialized["vec"] = _coerce_vector_to_list(vec_value)

    date_value = serialized.get("date_updated")
    if hasattr(date_value, "isoformat"):
        serialized["date_updated"] = date_value.isoformat()
    elif date_value is not None:
        serialized["date_updated"] = str(date_value)

    return serialized


def _load_rows_by_page(conn: Connection, table_name: str, page_number: int) -> tuple[list[dict[str, Any]], bool]:
    """Fetch a page of tag rows sorted from most recent to oldest."""

    offset_value = (page_number - 1) * PAGE_SIZE
    statement = text(
        f"""
SELECT id, word, vec, date_updated
FROM public.{table_name}
ORDER BY date_updated DESC, id DESC
LIMIT :limit_value
OFFSET :offset_value
"""
    )
    rows = list(
        conn.execute(
            statement,
            {"limit_value": PAGE_SIZE + 1, "offset_value": offset_value},
        ).mappings()
    )

    has_next = len(rows) > PAGE_SIZE
    visible_rows = rows[:PAGE_SIZE]
    serialized_rows = [_serialize_tag_row(row) for row in visible_rows]
    return serialized_rows, has_next


def _load_row_for_word(conn: Connection, table_name: str, word: str) -> Optional[dict[str, Any]]:
    """Retrieve a single row for the provided word if it exists."""

    statement = text(
        f"""
SELECT id, word, vec, date_updated
FROM public.{table_name}
WHERE word = :word
LIMIT 1
"""
    )
    result = conn.execute(statement, {"word": word}).mappings().first()
    if not result:
        return None
    return _serialize_tag_row(result)


@bp.get("/taglist/<selector>")
def fetch_tag_list(selector: str):
    """Return either a paginated list of tags or a greedy similarity chain."""

    ai = EmbeddingAi()
    engine = get_engine()
    table_name = ensure_tag_words_table_exists(ai=ai, engine=engine)

    is_page_request = selector.isdigit()
    page_number = max(int(selector), 1) if is_page_request else 1

    if is_page_request:
        with engine.begin() as conn:
            rows, has_next = _load_rows_by_page(conn, table_name, page_number)
        payload = {
            "ok": True,
            "mode": "page",
            "page": page_number,
            "pageSize": PAGE_SIZE,
            "hasNext": has_next,
            "hasPrevious": page_number > 1,
            "entries": rows,
        }
        return jsonify(payload)

    chain_entries = build_greedy_chain(selector)
    serialized_entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    with engine.begin() as conn:
        origin_row = _load_row_for_word(conn, table_name, selector)
        if origin_row:
            serialized_entries.append(origin_row)
            identifier = origin_row.get("id")
            if isinstance(identifier, str):
                seen_ids.add(identifier)

        for candidate in chain_entries:
            candidate_word = str(candidate.get("word") or "").strip()
            if not candidate_word:
                continue

            row = _load_row_for_word(conn, table_name, candidate_word)
            if not row:
                continue

            identifier = row.get("id")
            if isinstance(identifier, str):
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)

            if "embedding_distance" in candidate:
                row["embedding_distance"] = candidate.get("embedding_distance")

            serialized_entries.append(row)

    payload = {
        "ok": True,
        "mode": "chain",
        "seed": selector,
        "entries": serialized_entries,
    }
    return jsonify(payload)

@bp.delete("/metatag/<uuid_value>")
def delete_tag(uuid_value: str):
    """Remove a tag from the active metatext table."""

    ai = EmbeddingAi()
    engine = get_engine()
    table_name = ensure_tag_words_table_exists(ai=ai, engine=engine)
    normalized_id = normalize_pg_uuid(uuid_value)

    with engine.begin() as conn:
        statement = text(f"DELETE FROM public.{table_name} WHERE id = :identifier RETURNING id")
        result = conn.execute(statement, {"identifier": normalized_id})
        deleted_row = result.first()

    if not deleted_row:
        return jsonify({"ok": False, "error": "Tag not found."}), 404

    return jsonify({"ok": True, "id": normalized_id})


@bp.post("/taglist")
def add_tags():
    """Insert or update metatext entries using the shared helper."""

    payload = request.get_json(silent=True) or {}
    text_value = payload.get("text") or payload.get("input") or payload.get("value")
    if not isinstance(text_value, str) or not text_value.strip():
        return jsonify({"ok": False, "error": "Please provide tag text to insert."}), 400

    try:
        result_text = update_metatext(text_value)
    except Exception as exc:
        logging.getLogger(__name__).exception("Failed to update metatext tags")
        return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": True, "result": result_text})


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
