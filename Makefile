.PHONY: run test lint fmt corpus-stats

BASE_URL ?= http://localhost:8200

run:
	uv run uvicorn keystone_counsel.api:app --host 0.0.0.0 --port 8200 --reload

test:
	uv run pytest tests/ -v

lint:
	uv run ruff check src/ tests/

fmt:
	uv run ruff format src/ tests/

corpus-stats:
	@echo "Corpus files:"
	@find data/corpus -name "*.md" -type f 2>/dev/null | while read f; do echo "  $$f"; done
	@echo ""
	@echo "Corpus categories:"
	@ls -d data/corpus/*/ 2>/dev/null | while read d; do echo "  $$(basename $$d): $$(find $$d -name '*.md' | wc -l) files"; done

eval:
	uv run python -m keystone_counsel.eval

eval-run:
	uv run python -m keystone_counsel.eval $(BASE_URL)
