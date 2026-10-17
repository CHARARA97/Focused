"""Application services: orchestration over the pure domain and adapters.

Each module here answers one question (rules, session, scan, state) and is kept
small enough to test without the engine.  ``engine.py`` wires them together and
remains the compatibility facade during the refactor.
"""
