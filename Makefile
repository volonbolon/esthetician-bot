.PHONY: check

check:
	uv run ruff check .
	uv run pytest
