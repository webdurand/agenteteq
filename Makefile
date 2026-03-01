.PHONY: setup dev start test-cli front-setup front

setup:
	python3 -m venv .venv
	.venv/bin/pip install uv
	.venv/bin/uv pip install -r requirements.txt

dev:
	.venv/bin/fastapi dev src/main.py

start:
	.venv/bin/uvicorn src.main:app --port $${PORT:-8000} --host 0.0.0.0 --workers 1

test-cli:
	PYTHONPATH=. .venv/bin/python src/testing/cli.py

front-setup:
	cd ../agenteteq-front && npm install

front:
	cd ../agenteteq-front && npm run dev
