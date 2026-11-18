.PHONY: all install dev build frontend static clean distclean docker

all: build

frontend:
	cd frontend && npm ci && npm run build

static: frontend
	@mkdir -p src/crabagent/static
	rm -rf src/crabagent/static/assets
	cp -R frontend/dist/index.html frontend/dist/collab-browser.html frontend/dist/assets src/crabagent/static/

install: static
	pip install -e '.[dev]'

dev: install

build: static
	pip install build
	python -m build

clean:
	rm -rf src/crabagent/static
	rm -rf frontend/dist

distclean: clean
	rm -rf *.egg-info
	rm -rf dist
	rm -rf .eggs

docker:
	docker compose build
