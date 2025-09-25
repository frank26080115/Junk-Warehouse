export const CONTAINMENT_BIT = 1;
export const RELATED_BIT = 2;
export const SIMILAR_BIT = 4;
export const MERGE_BIT = 8;

export const ALL_ASSOCIATION_BITS = [
  CONTAINMENT_BIT,
  RELATED_BIT,
  SIMILAR_BIT,
  MERGE_BIT,
] as const;
export const ALL_ASSOCIATION_MASK = ALL_ASSOCIATION_BITS.reduce(
  (mask, bit) => mask | bit,
  0,
);

const BIT_TO_WORD: Record<number, string> = {
  [CONTAINMENT_BIT]: "containment",
  [RELATED_BIT]: "related",
  [SIMILAR_BIT]: "similar",
  [MERGE_BIT]: "merge",
};
const WORD_TO_BIT: Record<string, number> = Object.entries(BIT_TO_WORD).reduce<
  Record<string, number>
>((accumulator, [bit, word]) => {
  accumulator[word] = Number(bit);
  return accumulator;
}, {});

const BIT_TO_EMOJI_HTML_ENTITY: Record<number, string> = {
  [CONTAINMENT_BIT]: "&#x1F5C3;",
  [RELATED_BIT]: "&#x1F517;",
  [SIMILAR_BIT]: "&#x1F46F;",
  [MERGE_BIT]: "&#x1F91D;",
};
const BIT_TO_EMOJI_CHARACTER: Record<number, string> = {
  [CONTAINMENT_BIT]: "🗃️",
  [RELATED_BIT]: "🔗",
  [SIMILAR_BIT]: "👯",
  [MERGE_BIT]: "🤝",
};

export function bit_to_word(bit: number): string {
  return BIT_TO_WORD[bit] ?? "";
}

export function word_to_bit(word: string | null | undefined): number {
  if (!word) {
    return 0;
  }
  const normalized = word.trim().toLowerCase();
  return WORD_TO_BIT[normalized] ?? 0;
}

export function bit_to_emoji_html_entity(bit: number): string {
  return BIT_TO_EMOJI_HTML_ENTITY[bit] ?? "";
}

export function bit_to_emoji_character(bit: number): string {
  return BIT_TO_EMOJI_CHARACTER[bit] ?? "";
}

export function int_has_containment(value: number): boolean {
  return (value & CONTAINMENT_BIT) === CONTAINMENT_BIT;
}

export function int_has_related(value: number): boolean {
  return (value & RELATED_BIT) === RELATED_BIT;
}

export function int_has_similar(value: number): boolean {
  return (value & SIMILAR_BIT) === SIMILAR_BIT;
}

export function int_has_merge(value: number): boolean {
  return (value & MERGE_BIT) === MERGE_BIT;
}

export function collect_words_from_int(value: number): string[] {
  return ALL_ASSOCIATION_BITS.filter((bit) => (value & bit) === bit).map((bit) =>
    BIT_TO_WORD[bit],
  );
}

export function collect_emoji_characters_from_int(value: number): string[] {
  return ALL_ASSOCIATION_BITS.filter((bit) => (value & bit) === bit).map(
    (bit) => BIT_TO_EMOJI_CHARACTER[bit],
  );
}

export function collect_emoji_entities_from_int(value: number): string[] {
  return ALL_ASSOCIATION_BITS.filter((bit) => (value & bit) === bit).map(
    (bit) => BIT_TO_EMOJI_HTML_ENTITY[bit],
  );
}
