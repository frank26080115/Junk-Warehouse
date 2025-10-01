# Junk-Warehouse

DIY home inventory database and search system. Or... how a LLM can make your hoarding and OCD much worse.

The main idea behind this database is that any item can also be a container. Through their relationships, I can search for items and find where they are stored.

The search mechanism features the usage of semantic embeddings, plus directives and filters are implemented in a way to make the search page extremely multifunctional.

To encourage me to use the system and not get lazy when buying things, my emails (Gmail and IMAP) are scraped for anything that looks like an invoice and the items are scraped out of them. While most invoices are scraped using a best-guess strategy, invoices from Amazon, Digi-Key, and McMaster-Carr are processed in a much more algorithmic method. In fact, the Digi-Key developer API is being used as well.

This project involves a frontend built with Node.js + React, styled with Bootstrap. The backend is built with Python + Flask + PostgreSQL. OpenAI's ChatGPT and Codex were instrumental in constructing this project.
