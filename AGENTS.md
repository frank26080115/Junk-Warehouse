# AGENT GUIDELINES



These instructions apply to the entire repository.



## Workflow Expectations

- Prioritize rapid code delivery: prefer straightforward, readable implementations over experimental optimizations or refactors.

- Include descriptive, helpful comments that clarify intent; be explicit and avoid terse or obfuscated phrasing.

- Write code in a verbose, intent-revealing style rather than abbreviated or "hax0r" syntax.

- Never update database schema, only provide suggestions on database change when absolutely necessary.


## File Formatting

- Default to Windows-style line endings (CRLF) for all files, except for scripts intended to run strictly on Unix-like systems (e.g., `*.sh`), which should keep LF endings.



## Tooling and Commands

- Do **not** install npm, pip, or other language packages during environment initialization. Avoid running package installation commands unless explicitly instructed by the user.

- Skip running automated tests or lint commands unless the user specifically requests them.



## Technology Notes

- The frontend uses Node.js, React, and Bootstrap.

- The backend uses Python, Flask, PostgreSQL, and SQLAlchemy.
