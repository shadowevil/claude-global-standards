"""Shared library for the global write-guard hook. Stdlib only; nothing here imports anything
project-specific. Split by concern: paths (root resolution, normalization, allow-list), shell
(command segmentation and tokenizing), output (deny JSON, ASCII logging)."""
