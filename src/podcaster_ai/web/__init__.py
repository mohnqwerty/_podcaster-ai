"""podcaster_ai.web — FastAPI dashboard for browsing and triggering episodes.

This package is intentionally separate from the core pipeline modules:
- The pipeline can run without the dashboard (DB integration is opt-in and fail-soft).
- The dashboard never imports `podcaster_ai.run` directly; it shells out to
  `python -m podcaster_ai.run` so a long-running web process and a one-shot
  pipeline stay decoupled.
"""

__all__ = ["main", "db", "models", "auth", "deps", "jobs", "bootstrap"]
