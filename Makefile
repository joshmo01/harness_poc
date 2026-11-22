.PHONY: setup data pin run web test e2e up down
setup:          ## install Python and web dependencies
	pip install -r requirements.txt
	cd web && npm ci
data:           ## regenerate synthetic data
	python -m poc.data.generate
pin:            ## re-pin approved tool definitions into registry.yaml (CI only in real life)
	python -m poc.gateway.registry --pin
run:            ## stubs :8101, gateway :8100, BFF :8000
	python -m poc.run_all
web:            ## React app on :5173 (proxies /api to the BFF)
	cd web && npm run dev
test:           ## governance tests
	pytest -q tests/test_gateway.py
e2e:            ## browser walkthrough; needs `make run` and the web app running
	python tests/e2e/demo_walkthrough.py
up:             ## everything in Docker -> http://localhost:5173
	docker compose up --build
down:
	docker compose down -v
