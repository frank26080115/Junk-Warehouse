Directives superceeds all filters, they either dictate how the SQL is done, or how the results are presented.

Filters are only performed on rows returned from the database.

For example:

"* \orphans \showall ?C ?D | ?!E ?F"

With the "*" it means all items, no need for keyword search. The directive "orphans" will find items without relationships. "showall" will return a longer list than default. Conditions met must be "if ((C and D) or (not E and F))".

As a weak rule, directives are probably used for where relationships matter

## Directives

A directive starts with `\` and can optionally have a left-hand-side and right-hand-side delimited by `:`

## Filters

A filter starts with `?`, or `?!` for inverted logic, and can optionally have a left-hand-side and right-hand-side delimited by a few conditional operators such as `=` `<` `>` `[`. The `[` is for "in", but the left hand side is the haystack.

## List of Directives

| directive | function |
|-----------|----------|
| showall   | return as many results as possible |
| show:x    | return x number of results |
| page:x    | if too many results, return the x-th page |
| bydate    | order by creation date |
| bydatem   | order by modification date |
| byrand    | randomize sort |
| orderrev  | reverse order |
| smart     | use embeddings if possible |
| dumb      | do not use embeddings |


## List of Filters

| filter | function |
|--------|----------|
| orphans     | find items without relationships |
| uncontained | find items without containment relationships |
| alarm       | find items that have an alarm set and have passed the alarm date |
| staging     | find items with is_staging flag set |
| deleted     | find items with is_deleted flag set |
| has_invoice | find items that has an invoice related |
| has_image   | find items that has an image related |

The above is not comprehensive

Any boolean flag in the table column can be used for a filter, if the filter word when prepended with `is_` exists as a boolean table column, then it can be used as a filter. For example "staging" means check the `is_staging` column.

If the filter looks like `has_<column>` then the column must have a non-null non-whitespace non-empty value.
