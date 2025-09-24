#!/usr/bin/env bash
set -euo pipefail

mapfile -t files < <(git ls-files --cached --others --exclude-standard)

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No files to process."
  exit 0
fi

updated=()

for file in "${files[@]}"; do
  if [[ ! -f "$file" ]]; then
    continue
  fi

  if LC_ALL=C grep -q $'\0' -- "$file"; then
    continue
  fi

  if ! LC_ALL=C grep -q $'\r\n' -- "$file"; then
    continue
  fi

  tmp_file=$(mktemp)
  perl -0pe 's/\r\n?|\n/\r\n/g' -- "$file" >"$tmp_file"

  if ! cmp -s "$file" "$tmp_file"; then
    mv "$tmp_file" "$file"
    updated+=("$file")
  else
    rm "$tmp_file"
  fi
done

if [[ ${#updated[@]} -gt 0 ]]; then
  echo "Updated line endings for:"
  for file in "${updated[@]}"; do
    echo " - $file"
  done
else
  echo "No files required updates."
fi
