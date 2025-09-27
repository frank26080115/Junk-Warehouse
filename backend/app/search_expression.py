import logging
import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .db import get_column_types, get_engine

log = logging.getLogger(__name__)


_TABLE_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "items": {
        "default_alias": "i",
        "db_table": "items",
        "order_columns": {
            "bydate": "date_creation",
            "bydatem": "date_last_modified",
        },
    },
    "invoices": {
        "default_alias": "inv",
        "db_table": "invoices",
        "order_columns": {
            "bydate": "date",
            "bydatem": "date",
        },
    },
}

_DYNAMIC_COLUMN_TYPES_CACHE: Dict[str, Dict[str, str]] = {}


def _normalize_column_type(type_str: str) -> str:
    normalized = (type_str or "").strip().lower()
    if not normalized:
        return "text"
    if "bool" in normalized:
        return "boolean"
    if "uuid" in normalized:
        return "uuid"
    if "timestamp" in normalized or normalized == "date" or normalized.endswith(" date"):
        return "timestamp"
    if any(token in normalized for token in ("numeric", "decimal", "real", "double", "float", "money")):
        return "numeric"
    if any(token in normalized for token in ("int", "serial")):
        return "integer"
    if "tsvector" in normalized:
        return "tsvector"
    if any(
        token in normalized
        for token in ("text", "char", "clob", "string", "json", "enum", "bytea", "citext")
    ):
        return "text"
    return normalized


def _introspect_column_types(table_identifier: str) -> Dict[str, str]:
    cached = _DYNAMIC_COLUMN_TYPES_CACHE.get(table_identifier)
    if cached is not None:
        return dict(cached)
    if not table_identifier:
        return {}
    try:
        engine = get_engine()
    except Exception:
        log.debug("Unable to acquire engine for column introspection", exc_info=True)
        return {}
    try:
        raw_types = get_column_types(engine, table_identifier)
    except Exception:
        log.debug("Column introspection failed for table '%s'", table_identifier, exc_info=True)
        return {}
    normalized = {column: _normalize_column_type(type_str) for column, type_str in raw_types.items()}
    _DYNAMIC_COLUMN_TYPES_CACHE[table_identifier] = normalized
    return dict(normalized)


def _resolve_column_types(_schema_key: str, schema: Dict[str, Any], actual_table: str) -> Dict[str, str]:
    column_types: Dict[str, str] = {}

    def _add_candidate(candidates: List[str], candidate: Optional[str]) -> None:
        if isinstance(candidate, str):
            candidate_trimmed = candidate.strip()
            if candidate_trimmed and candidate_trimmed not in candidates:
                candidates.append(candidate_trimmed)

    candidates: List[str] = []
    _add_candidate(candidates, actual_table)
    _add_candidate(candidates, schema.get("db_table"))

    for identifier in candidates:
        dynamic_types = _introspect_column_types(identifier)
        if not dynamic_types:
            continue
        for column, column_type in dynamic_types.items():
            if column not in column_types:
                column_types[column] = column_type
        if column_types:
            break

    return column_types

# --- helpers (same as before) ---
def _coerce_literal(s: str) -> Any:
    if s is None:
        return None
    s = s.strip()
    if (len(s) >= 2) and ((s[0] == s[-1]) and s[0] in ("'", '"')):
        return s[1:-1]
    try:
        return json.loads(s)
    except Exception:
        pass
    try:
        if re.fullmatch(r"[+-]?\d+", s):
            return int(s)
        if re.fullmatch(r"[+-]?\d*\.\d+", s):
            return float(s)
    except Exception:
        pass
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low in ("none", "null"):
        return None
    return s

def _is_iterable_but_not_str(x: Any) -> bool:
    return isinstance(x, Iterable) and not isinstance(x, (str, bytes, bytearray))


_DATE_FORMATS: Tuple[str, ...] = (
    "%Y/%m/%d",
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
)


def _normalize_date_literal(value: Any) -> Any:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            for fmt in _DATE_FORMATS:
                try:
                    parsed = datetime.strptime(candidate, fmt)
                except ValueError:
                    continue
                return parsed.strftime("%Y-%m-%d")
    return value


def get_sql_order_and_limit(
    criteria: Mapping[str, Any],
    *,
    alias: str,
    use_textsearch: bool,
    rank_expression: Optional[str],
    default_order_templates: Optional[Iterable[str]],
    default_limit: int,
) -> Tuple[List[str], Optional[int], Optional[int]]:
    flags = criteria.get("flags") or {}
    order_by_clauses: List[str] = list(criteria.get("order_by") or [])
    if order_by_clauses:
        if use_textsearch and rank_expression and not flags.get("random_order"):
            order_by_clauses.append(f"{rank_expression} DESC")
    else:
        base_direction = "ASC" if flags.get("reverse_default_order") else "DESC"
        if use_textsearch and rank_expression:
            order_by_clauses.append(f"{rank_expression} {base_direction}")
        if default_order_templates:
            for template in default_order_templates:
                order_by_clauses.append(template.format(alias=alias, direction=base_direction))

    limit_value = criteria.get("limit")
    limit_is_explicit = criteria.get("limit_is_explicit", False)
    page_number = criteria.get("page")
    show_all = flags.get("show_all", False)

    if not limit_is_explicit:
        limit_value = default_limit
    elif show_all:
        limit_value = None

    offset_value: Optional[int] = None
    if isinstance(page_number, int) and page_number > 1 and limit_value:
        offset_value = (page_number - 1) * limit_value

    return order_by_clauses, limit_value, offset_value


class DirectiveUnit:
    _PATTERN = re.compile(r"^\\(?P<lhs>[^\s=]+)(?:=(?P<rhs>.+))?$", re.VERBOSE)

    def __init__(self, token: str, context: Any):
        self.context = context
        self.raw = token
        self.lhs: Optional[str] = None
        self.rhs: Any = None
        self.rhs_raw: Optional[str] = None
        self._parse_error = False
        m = self._PATTERN.match(token.strip())
        if not m:
            self._parse_error = True
            log.warning("DirectiveUnit parse failed for token %r", token)
            return
        self.lhs = m.group("lhs").strip().lower()
        self.rhs_raw = (m.group("rhs") or "").strip() if m.group("rhs") is not None else None
        self.rhs = _coerce_literal(self.rhs_raw) if self.rhs_raw is not None else None

    def has_value(self) -> bool:
        return self.rhs_raw is not None

    def parts(self, delim: str = ":") -> List[str]:
        if not isinstance(self.rhs_raw, str):
            return []
        return [p for p in self.rhs_raw.split(delim) if p != ""]

    def ensure_valid(self) -> bool:
        return not self._parse_error and self.lhs is not None

    def __repr__(self) -> str:
        return f"DirectiveUnit(lhs={self.lhs!r}, rhs={self.rhs!r}, raw={self.raw!r})"


# --------------------- FilterUnit ---------------------
class FilterUnit:
    _PATTERN = re.compile(r"^\?(?P<neg>!)?(?P<key>[^\s=\[<>]+)(?:(?P<op>[=\[<>])(?P<rhs>.+)?)?$", re.VERBOSE)
    def __init__(self, token: str, parent_query: "SearchQuery", context: Any):
        self.parent_query = parent_query
        self.context = context
        m = self._PATTERN.match(token.strip())
        if not m:
            self.negated = False; self.key = None; self.op = None; self.rhs = None
            self._raw = token; self._parse_error = True
            log.warning("FilterUnit parse failed for token %r", token); return
        self._parse_error = False
        self._raw = token
        self.negated = bool(m.group("neg"))
        self.key = m.group("key")
        self.op = m.group("op")
        rhs_raw = m.group("rhs")
        if self.op is None:
            self.rhs = None
        else:
            if self.op == "[" and rhs_raw is not None:
                rhs_trim = rhs_raw.strip()
                if rhs_trim.endswith("]"):
                    rhs_trim = rhs_trim[:-1]
                self.rhs = _coerce_literal(rhs_trim)
            else:
                self.rhs = _coerce_literal(rhs_raw)

    def evaluate(self, row: Dict[str, Any]) -> bool:
        try:
            if self._parse_error or self.key is None:
                log.error("Skipping invalid FilterUnit: %r", self._raw)
                return False
            handler = self.parent_query.predicates.get(self.key)
            if handler:
                result = bool(handler(row=row, op=self.op, rhs=self.rhs, context=self.context))
                return (not result) if self.negated else result
            value = row.get(self.key, None)
            if self.op is None:
                result = bool(value)
            elif self.op == "=":
                result = (value == self.rhs)
            elif self.op == ">":
                try:
                    lv = float(value) if isinstance(value, (int, float, str)) and str(value).strip() != "" else value
                    rv = float(self.rhs) if isinstance(self.rhs, (int, float, str)) and str(self.rhs).strip() != "" else self.rhs
                    result = lv > rv if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) else str(value) > str(self.rhs)
                except Exception:
                    log.exception("Failed '>' compare: value=%r rhs=%r", value, self.rhs); return False
            elif self.op == "<":
                try:
                    lv = float(value) if isinstance(value, (int, float, str)) and str(value).strip() != "" else value
                    rv = float(self.rhs) if isinstance(self.rhs, (int, float, str)) and str(self.rhs).strip() != "" else self.rhs
                    result = lv < rv if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) else str(value) < str(self.rhs)
                except Exception:
                    log.exception("Failed '<' compare: value=%r rhs=%r", value, self.rhs); return False
            elif self.op == "[":
                if value is None:
                    result = False
                elif _is_iterable_but_not_str(value):
                    result = (self.rhs in value)
                else:
                    try:
                        result = (self.rhs in value)
                    except Exception:
                        log.exception("Failed '[' membership: value=%r rhs=%r", value, self.rhs); return False
            else:
                log.warning("Unknown operator %r in token %r", self.op, self._raw); return False
            return (not result) if self.negated else result
        except Exception:
            log.exception("FilterUnit evaluation error for token %r on row %r", self._raw, row)
            return False

    def __repr__(self) -> str:
        return f"FilterUnit(raw={self._raw!r})"


class SearchQuery:
    """
    Prefix (before first '?'):
      - split by whitespace
      - '\\...' -> DirectiveUnit
      - everything else: attempt to extract UUIDs / short_ids / slugs; leftover terms feed free-text search

    Suffix (from first '?'):
      - same filter grammar: OR by '|', AND inside a chain (tokens starting with '?')
    """

    # case-insensitive regexes
    _UUID_HYPHENED = re.compile(
        r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    _UUID_COMPACT = re.compile(r"(?i)^[0-9a-f]{32}$")
    _SHORT_ID     = re.compile(r"(?i)^[0-9a-f]{8}$")

    def __init__(self, s: str, context: Any):
        self.context = context
        self.raw: str = s or ""

        # Output fields you care about:
        self.identifiers: List[str] = []      # ordered list of uuid/short_id (normalized)
        self.query_terms: List[str] = []      # remaining terms for text search
        self.query_text: str = ""             # " ".join(query_terms) after slug expansion
        self.directive_units: List[DirectiveUnit] = []

        self.filters_raw: str = ""
        self._chains: List[List[FilterUnit]] = []
        self.predicates: Dict[str, Any] = {}

        prefix, self.filters_raw = self._split_query_and_filters(self.raw)
        terms_raw, self.directive_units = self._parse_prefix(prefix)

        # NEW: extract IDs and clean up terms (incl. slug expansion)
        self.query_terms, self.identifiers = self._extract_ids_and_clean_terms(terms_raw)
        self.query_text = " ".join(self.query_terms)

        if self.filters_raw:
            self._chains = self._parse_filter_chains(self.filters_raw)

    @staticmethod
    def _split_query_and_filters(s: str) -> Tuple[str, str]:
        idx = s.find("?")
        if idx == -1:
            return s.strip(), ""
        return s[:idx].strip(), s[idx:].strip()

    def _parse_prefix(self, prefix: str) -> Tuple[List[str], List[DirectiveUnit]]:
        if not prefix:
            return [], []
        terms: List[str] = []
        directives: List[DirectiveUnit] = []
        for tok in prefix.split():
            if tok.startswith("\\") and len(tok) > 1:
                try:
                    directives.append(DirectiveUnit(tok, context=self.context))
                except Exception:
                    log.exception("Failed to create DirectiveUnit for token %r", tok)
            else:
                terms.append(tok)
        return terms, directives

    # ---------- NEW: ID extraction + slug handling ----------
    @classmethod
    def _uuid_canonical(cls, s: str) -> str:
        """Normalize a UUID (with or without hyphens) to lowercase hyphenated canonical form."""
        hex32 = s.replace("-", "").lower()
        # assume already validated as 32 hex chars
        return f"{hex32[0:8]}-{hex32[8:12]}-{hex32[12:16]}-{hex32[16:20]}-{hex32[20:32]}"

    @classmethod
    def _looks_like_uuid(cls, token: str) -> Optional[str]:
        t = token.strip()
        if cls._UUID_HYPHENED.match(t):
            return cls._uuid_canonical(t)
        if cls._UUID_COMPACT.match(t):
            return cls._uuid_canonical(t)
        return None

    @classmethod
    def _looks_like_short_id(cls, token: str) -> Optional[str]:
        """Return normalized short_id (8 hex, lowercase) if the entire token is a short_id."""
        t = token.strip()
        if cls._SHORT_ID.match(t):
            return t.lower()
        return None

    @staticmethod
    def _slug_split_words(body: str) -> List[str]:
        """
        Convert the 'word-word--word' body to words:
          - Single '-' -> space
          - Double '--' -> literal hyphen
        """
        SENTINEL = "\u0000"
        tmp = body.replace("--", SENTINEL)
        tmp = tmp.replace("-", " ")
        tmp = tmp.replace(SENTINEL, "-")
        # Collapse multiple spaces then split
        return [w for w in tmp.split() if w]

    @classmethod
    def _looks_like_slug_with_short_id(cls, token: str) -> Optional[Tuple[List[str], str]]:
        """
        If token matches '<body>-<short_id>' (last 8 hex after a hyphen),
        return (words_from_body, normalized_short_id). Otherwise None.
        """
        t = token.strip()
        if "-" not in t:
            return None
        # Ensure there's '-<8hex>' at the end
        maybe_sid = t[-8:]
        pre = t[:-8]
        if len(pre) >= 1 and pre[-1] == "-" and cls._SHORT_ID.match(maybe_sid):
            body = pre[:-1]  # drop the hyphen before the sid
            words = cls._slug_split_words(body)
            return (words, maybe_sid.lower())
        return None

    def _extract_ids_and_clean_terms(self, terms_in: List[str]) -> Tuple[List[str], List[str]]:
        """
        Scan tokens left-to-right, extract UUIDs / short_ids / slugs.
        - UUIDs recognized first (so we don't pull short_ids out of them).
        - Slug '...-deadbeef' yields words + short_id.
        - Plain 8-hex tokens become short_ids.
        - All extracted ID tokens are removed from free-text terms.
        Returns (clean_terms, identifiers_in_order).
        """
        clean_terms: List[str] = []
        ids: List[str] = []

        for tok in terms_in:
            # 1) UUID (hyphened or compact)
            u = self._looks_like_uuid(tok)
            if u:
                ids.append(u)
                continue

            # 2) Slug with short_id at the end
            slug_hit = self._looks_like_slug_with_short_id(tok)
            if slug_hit:
                words, sid = slug_hit
                ids.append(sid)
                clean_terms.extend(words)
                continue

            # 3) Plain short_id (exact 8 hex token)
            sid = self._looks_like_short_id(tok)
            if sid:
                ids.append(sid)
                continue

            # 4) Otherwise keep as a regular term
            clean_terms.append(tok)

        return clean_terms, ids

    # -------- filters (same as before) --------
    def _parse_filter_chains(self, filters_raw: str) -> List[List[FilterUnit]]:
        chains: List[List[FilterUnit]] = []
        for chain_str in filters_raw.split("|"):
            chain_str = chain_str.strip()
            if not chain_str:
                continue
            tokens = chain_str.split()
            units: List[FilterUnit] = []
            for tok in tokens:
                if tok.startswith("?"):
                    try:
                        units.append(FilterUnit(tok, parent_query=self, context=self.context))
                    except Exception:
                        log.exception("Failed to create FilterUnit for token %r", tok)
                else:
                    log.debug("Ignoring non-filter token in chain: %r", tok)
            if units:
                chains.append(units)
        return chains

    @property
    def chains(self) -> List[List[FilterUnit]]:
        return self._chains

    def directives_as_kv(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {}
        for du in self.directive_units:
            if du.ensure_valid():
                d[du.lhs] = du.rhs if du.has_value() else True
        return d


    def has_directive(self, directive_name: str) -> bool:
        """Return True when a directive token with the requested name is present."""
        # Validate the directive name early so the method is safe for any caller.
        if not isinstance(directive_name, str):
            return False
        directive_key = directive_name.strip().lower()
        if not directive_key:
            return False
        # Walk through each parsed directive token and use the normalized left-hand
        # side value for comparison. Invalid tokens are ignored because
        # DirectiveUnit.ensure_valid() reports any parsing issues.
        for directive_unit in self.directive_units:
            if not directive_unit.ensure_valid():
                continue
            lhs_value = (directive_unit.lhs or "").strip().lower()
            if lhs_value == directive_key:
                return True
        return False


    def get_sql_conditionals(self) -> Dict[str, Any]:
        table = "items"
        alias: Optional[str] = None
        if isinstance(self.context, dict):
            ctx_table = self.context.get("table")
            if isinstance(ctx_table, str) and ctx_table.strip():
                table = ctx_table.strip().lower()
            alias_value = self.context.get("table_alias")
            if isinstance(alias_value, str) and alias_value.strip():
                alias = alias_value.strip()

        schema_key = table
        if schema_key not in _TABLE_SCHEMAS and "." in schema_key:
            schema_key = schema_key.split(".", 1)[1]
        if schema_key not in _TABLE_SCHEMAS:
            schema_key = "items"

        schema = _TABLE_SCHEMAS[schema_key]
        if not alias:
            alias = schema.get("default_alias", "t")
        column_types: Dict[str, str] = _resolve_column_types(schema_key, schema, table)
        boolean_columns: Set[str] = {name for name, typ in column_types.items() if typ == "boolean"}
        text_columns: Set[str] = {name for name, typ in column_types.items() if typ == "text"}
        comparable_columns: Set[str] = {
            name for name, typ in column_types.items() if typ in ("integer", "numeric", "timestamp")
        }
        order_columns: Dict[str, str] = schema.get("order_columns", {})

        params: Dict[str, Any] = {}
        touched_columns: Set[str] = set()
        residual_chains: List[List[FilterUnit]] = []
        applied_filters: List[str] = []

        def _new_param(value: Any) -> str:
            name = f"sq_param_{len(params)}"
            params[name] = value
            return name

        def _wrap_condition(condition: str, negated: bool) -> str:
            return f"NOT ({condition})" if negated else condition

        def _coerce_rhs_bool(value: Any) -> Optional[bool]:
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                low = value.strip().lower()
                if low in {"true", "1"}:
                    return True
                if low in {"false", "0"}:
                    return False
            if isinstance(value, (int, float)):
                if value in (0, 1):
                    return bool(value)
            return None

        def _condition_for_column(column: str, column_type: str, unit: FilterUnit) -> Optional[str]:
            qualified = f"{alias}.{column}"
            if column_type == "boolean":
                if unit.op is None:
                    return f"{qualified} = {'FALSE' if unit.negated else 'TRUE'}"
                if unit.op == "=":
                    if unit.rhs is None:
                        if unit.negated:
                            return f"{qualified} IS NOT NULL"
                        return f"{qualified} IS NULL"
                    rhs_bool = _coerce_rhs_bool(unit.rhs)
                    if rhs_bool is None:
                        return None
                    param_name = _new_param(rhs_bool)
                    if unit.negated:
                        return f"{qualified} <> :{param_name}"
                    return f"{qualified} = :{param_name}"
                return None
            if unit.op is None:
                return None
            if unit.op == "=":
                if unit.rhs is None:
                    if unit.negated:
                        return f"{qualified} IS NOT NULL"
                    return f"{qualified} IS NULL"
                rhs_value = unit.rhs
                if column_type == "timestamp":
                    rhs_value = _normalize_date_literal(rhs_value)
                param_name = _new_param(rhs_value)
                if unit.negated:
                    return f"{qualified} <> :{param_name}"
                return f"{qualified} = :{param_name}"
            if unit.op in (">", "<"):
                if column in comparable_columns and unit.rhs is not None:
                    rhs_value = unit.rhs
                    if column_type == "timestamp":
                        rhs_value = _normalize_date_literal(rhs_value)
                    param_name = _new_param(rhs_value)
                    condition = f"{qualified} {unit.op} :{param_name}"
                    return _wrap_condition(condition, unit.negated)
                return None
            if unit.op == "[":
                return None
            return None

        show_all = False
        explicit_limit = False
        limit_value: Optional[int] = None
        page_number: Optional[int] = None
        order_request: Optional[str] = None
        random_order = False
        reverse_toggle = False
        mode: Optional[str] = None
        directives_applied: List[str] = []

        for directive_unit in self.directive_units:
            if not directive_unit.ensure_valid():
                continue
            directive = (directive_unit.lhs or "").strip().lower()
            directives_applied.append(directive)
            if directive == "showall":
                show_all = True
                explicit_limit = True
                limit_value = None
            elif directive == "show":
                try:
                    count = int(directive_unit.rhs)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    limit_value = count
                    explicit_limit = True
            elif directive == "page":
                try:
                    page_val = int(directive_unit.rhs)
                except (TypeError, ValueError):
                    continue
                if page_val >= 1:
                    page_number = page_val
            elif directive == "bydate":
                order_request = "bydate"
            elif directive == "bydatem":
                order_request = "bydatem"
            elif directive == "byrand":
                random_order = True
            elif directive == "orderrev":
                reverse_toggle = not reverse_toggle
            elif directive == "smart":
                mode = "smart"
            elif directive == "dumb":
                mode = "dumb"

        order_by: List[str] = []
        reverse_default_order = False
        if random_order:
            order_by.append("random()")
        else:
            if order_request:
                column_name = order_columns.get(order_request)
                if column_name:
                    direction = "ASC" if reverse_toggle else "DESC"
                    order_by.append(f"{alias}.{column_name} {direction}")
                    reverse_toggle = False
            if not order_by and reverse_toggle:
                reverse_default_order = True

        flags: Dict[str, Any] = {
            "show_all": show_all,
            "random_order": random_order,
            "reverse_default_order": reverse_default_order,
        }
        if mode:
            flags["mode"] = mode
        if directives_applied:
            flags["directives"] = directives_applied

        chain_sqls: List[str] = []

        def _convert_filter_unit(unit: FilterUnit) -> Optional[str]:
            if unit.key is None:
                return None
            key_norm = unit.key.strip().lower()
            if not key_norm:
                return None
            if schema_key == "items":
                if key_norm == "orphans":
                    condition = (
                        f"NOT EXISTS (SELECT 1 FROM relationships AS r "
                        f"WHERE r.item_id = {alias}.id OR r.assoc_id = {alias}.id)"
                    )
                    return _wrap_condition(condition, unit.negated)
                if key_norm == "uncontained":
                    condition = (
                        f"NOT EXISTS (SELECT 1 FROM relationships AS r "
                        f"WHERE r.assoc_type = 'containment' AND r.assoc_id = {alias}.id)"
                    )
                    return _wrap_condition(condition, unit.negated)
                if key_norm == "alarm":
                    condition = (
                        f"({alias}.date_reminder IS NOT NULL AND {alias}.date_reminder <= now())"
                    )
                    touched_columns.add("date_reminder")
                    return _wrap_condition(condition, unit.negated)
                if key_norm == "has_invoice":
                    condition = (
                        f"EXISTS (SELECT 1 FROM invoice_items AS ii "
                        f"WHERE ii.item_id = {alias}.id)"
                    )
                    return _wrap_condition(condition, unit.negated)
                if key_norm == "has_image":
                    condition = (
                        f"EXISTS (SELECT 1 FROM item_images AS im "
                        f"WHERE im.item_id = {alias}.id)"
                    )
                    return _wrap_condition(condition, unit.negated)
            column_name: Optional[str] = None
            column_type: Optional[str] = None
            if key_norm in column_types:
                column_name = key_norm
                column_type = column_types[key_norm]
            else:
                candidate = f"is_{key_norm}"
                if candidate in boolean_columns:
                    column_name = candidate
                    column_type = "boolean"
            if column_name and column_type:
                condition = _condition_for_column(column_name, column_type, unit)
                if condition is not None:
                    touched_columns.add(column_name)
                    return condition
            if key_norm.startswith("has_"):
                column_candidate = key_norm[4:]
                if column_candidate in column_types:
                    qualified = f"{alias}.{column_candidate}"
                    if column_types[column_candidate] == "text" or column_candidate in text_columns:
                        condition = f"COALESCE(btrim({qualified}), '') <> ''"
                    else:
                        condition = f"{qualified} IS NOT NULL"
                    touched_columns.add(column_candidate)
                    return _wrap_condition(condition, unit.negated)
            return None

        for chain in self._chains:
            if not chain:
                continue
            chain_conditions: List[str] = []
            chain_keys: List[str] = []
            convertible = True
            for unit in chain:
                condition = _convert_filter_unit(unit)
                if condition is None:
                    convertible = False
                    break
                chain_conditions.append(condition)
                chain_keys.append(unit.key or "")
            if convertible and chain_conditions:
                chain_sqls.append(f"({' AND '.join(chain_conditions)})")
                applied_filters.extend(chain_keys)
            else:
                residual_chains.append(chain)

        where_clauses: List[str] = []
        if chain_sqls and not residual_chains:
            where_clauses.append(f"({' OR '.join(chain_sqls)})")
        else:
            if self._chains and not chain_sqls:
                residual_chains = list(self._chains)
            if residual_chains:
                applied_filters = []

        conditionals: Dict[str, Any] = {
            "table": table,
            "table_alias": alias,
            "where": where_clauses,
            "order_by": order_by,
            "limit": limit_value,
            "limit_is_explicit": explicit_limit,
            "page": page_number,
            "params": params,
            "flags": flags,
            "touched_columns": touched_columns,
            "applied_filters": applied_filters,
            "residual_chains": residual_chains,
        }
        return conditionals

    def evaluate(self, row: Dict[str, Any]) -> bool:
        try:
            if not self._chains:
                return True
            for chain in self._chains:
                try:
                    if all(unit.evaluate(row) for unit in chain):
                        return True
                except Exception:
                    log.exception("Error evaluating chain %r on row %r", chain, row)
                    continue
            return False
        except Exception:
            log.exception("SearchQuery.evaluate failed for row %r", row)
            return False

    def __repr__(self) -> str:
        return (
            f"SearchQuery(query_text={self.query_text!r}, "
            f"identifiers={self.identifiers!r}, "
            f"directive_units={self.directive_units!r}, "
            f"chains={self._chains!r})"
        )
