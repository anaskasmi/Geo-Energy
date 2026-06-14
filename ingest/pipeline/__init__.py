"""Ingestion pipeline (Service A): fetch → clean → reproject → build → atomic swap."""

__all__ = ["config", "crs", "db", "geoparquet", "fetchers", "harness", "logging_setup"]
