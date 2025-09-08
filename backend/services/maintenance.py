# use from project root
# python backend/tools/maintenance.py

import os
from pathlib import Path
from dotenv import load_dotenv

import logging
from app.logging_setup import start_log

# Load backend/.env explicitly (does nothing if file doesn't exist)
DOTENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(DOTENV_PATH, override=False)

def main():
    # Initialize logger using your existing setup
    logger = start_log(app_name="maintenance")

    while True:
        try:
            logger.info("Running maintenance task...")

            # TODO: Add your housekeeping logic here
            # e.g., cleaning old sessions, rotating temp files, etc.

        except Exception as e:
            logger.exception("Maintenance loop error")

        time.sleep(60)  # wait 1 minute

if __name__ == "__main__":
    main()
