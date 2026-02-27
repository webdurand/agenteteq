.PHONY: setup dev

setup:
	python3 -m venv .venv
	.venv/bin/pip install uv
	.venv/bin/uv pip install -r requirements.txt

dev:
	.venv/bin/fastapi dev src/main.py

start:
	.venv/bin/fastapi run src/main.py --port $${PORT:-8000} --host 0.0.0.0
