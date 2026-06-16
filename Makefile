# Site-Selection App — task runner (GEO-1)
# Thin wrappers over docker compose so the common workflow is one word.
# Usage: `make help`

COMPOSE ?= docker compose
# Profiles that gate the one-shot services (ingest, tiles, frontend). Needed so `build` and
# `config` see ALL services — compose honors profiles for those subcommands too.
ALL_PROFILES ?= --profile ingest --profile tiles --profile build

.DEFAULT_GOAL := help
.PHONY: help build ingest frontend tiles up down restart logs ps config clean test fmt \
        ci verify check-build tls-config tls-up

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

build: ## Build all service images (incl. profiled ingest + frontend)
	$(COMPOSE) $(ALL_PROFILES) build

ingest: ## Run the one-shot ingestion job (fetch → clean → reproject → build → swap)
	$(COMPOSE) run --rm ingest

frontend: ## Build the SPA into the web_dist volume
	$(COMPOSE) run --rm frontend

tiles: ## Generate parcels.pmtiles from the current release (GEO-14; tippecanoe bundled in the ingest image)
	$(COMPOSE) run --rm tiles

up: ## Start api + web (SPA on http://localhost:8080)
	$(COMPOSE) up -d api web

down: ## Stop and remove containers (named volumes are preserved)
	$(COMPOSE) down

restart: down up ## Restart the long-running services

logs: ## Tail logs for the running services
	$(COMPOSE) logs -f

ps: ## Show service status
	$(COMPOSE) ps

config: ## Validate & render the fully-resolved compose configuration (all 4 services)
	$(COMPOSE) $(ALL_PROFILES) config

test: ## Run the ingest test suite inside the ingest image
	$(COMPOSE) run --rm --entrypoint pytest ingest -q

clean: ## Remove containers AND the data/web_dist volumes (DESTRUCTIVE — rebuilds artifact)
	$(COMPOSE) down -v

ci: build test ## Local CI mirror (GEO-38): build all images + ingest tests. Full API/frontend matrix runs in GitHub Actions.

verify: ## Assert every service image runs as a non-root user (GEO-36)
	./deploy/verify-nonroot.sh

check-build: ## Show ingest build.success / build.failed events from the logs (GEO-37)
	./deploy/check-build.sh

tls-config: ## Validate the optional Caddy TLS edge merge (GEO-36)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.tls.yml config

tls-up: ## Start the stack WITH the Caddy TLS edge (set SITE_ADDRESS + ACME_EMAIL first; GEO-36)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.tls.yml up -d
