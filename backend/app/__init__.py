"""
Application package initializer.

Avoid side effects here—no network, DB, or logging setup.
"""

# Re-export so callers can do: from app import create_app (or app)
# from .main import create_app, app  # noqa: F401

__all__ = ["create_app", "app"]

"""
Benefits:

Lets you run Flask as:

python -m flask --app app:create_app run ... (factory)

or python -m flask --app app:app run ... (instance)

In code/tests you can just from app import create_app.

Otherwise, this entire file can be empty too.
"""
