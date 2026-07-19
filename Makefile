# ============================================================================
#  NCollection ERP — developer command shortcuts
# ============================================================================
#  Thin wrappers over `$(COMPOSE)`. Run `make help` for the list.
#  Full explanation: docs/markdown/LOCAL_DEV_AND_ARCHITECTURE.md
# ============================================================================

# Optionally pull DB credentials from .env (falls back to dev defaults).
-include .env

DB_USER     ?= odoo
DB_PASSWORD ?= odoo
DB_NAME     ?= postgres

# Target Odoo database for module operations. Override per-command, e.g.:
#   make upgrade m=ncollection_subscription db=ncollection_demo
db ?= ncollection

# Odoo needs DB connection args when invoked via `exec` (the entrypoint that
# normally injects them only runs for the container's main process).
ODOO_DB_ARGS = --db_host=db --db_user=$(DB_USER) --db_password=$(DB_PASSWORD)

# Compose files. The dev override (Nginx edge on :80 — P1-T03, plus pgAdmin) is
# ON by default for local `make` usage. CI and prod call compose directly with
# their own file sets, so this affects only `make`. Run the base stack alone with:
#   make up COMPOSE_FILES="-f docker-compose.yml"
COMPOSE_FILES ?= -f docker-compose.yml -f docker-compose.dev.yml
COMPOSE       ?= docker compose $(COMPOSE_FILES)

# Opt-in routing-verification stack (P1-T06): base + dev + the routing overlay
# (db_filter=^%d$, list_db=False). Used only by the `routing-*` targets — it does
# NOT affect `make up`.
ROUTING_COMPOSE ?= $(COMPOSE) -f docker-compose.routing.yml

.DEFAULT_GOAL := help
.PHONY: help up down stop restart logs ps shell psql odoo-shell \
        bootstrap createdb dropdb install upgrade demo \
        routing-up routing-verify routing-down routing-clean

help: ## Show this help
	@echo "NCollection ERP — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: db=<database> (default: $(db)), m=<module>"

## ---- Stack lifecycle ----
up: ## Start the dev stack (Odoo :8069, Nginx edge :80, pgAdmin :5050)
	$(COMPOSE) up -d

down: ## Stop and remove containers (keeps data volumes)
	$(COMPOSE) down

stop: ## Stop containers without removing them
	$(COMPOSE) stop

restart: ## Restart just the Odoo container (apply mounted code/config)
	$(COMPOSE) restart odoo

logs: ## Follow Odoo logs (Ctrl-C to exit)
	$(COMPOSE) logs -f odoo

ps: ## Show container status
	$(COMPOSE) ps

## ---- Shells ----
shell: ## Open a bash shell inside the Odoo container
	$(COMPOSE) exec odoo bash

psql: ## Open a psql prompt on the target database (db=...)
	$(COMPOSE) exec db psql -U $(DB_USER) -d $(db)

odoo-shell: ## Open an Odoo Python shell against the target database (db=...)
	$(COMPOSE) exec odoo odoo shell -d $(db) $(ODOO_DB_ARGS)

## ---- Database & modules ----
bootstrap: ## Create db=... and install the NCollection modules (first-run setup)
	$(COMPOSE) exec odoo odoo -d $(db) \
		-i base,ncollection_subscription,ncollection_branding \
		--stop-after-init $(ODOO_DB_ARGS)
	$(COMPOSE) restart odoo
	@echo "Done. Open http://localhost:8069  (select database '$(db)')"

createdb: ## Create and initialize an empty Odoo database (db=...)
	$(COMPOSE) exec odoo odoo -d $(db) -i base --stop-after-init $(ODOO_DB_ARGS)
	$(COMPOSE) restart odoo

dropdb: ## Drop the target database (db=...) — destructive
	$(COMPOSE) exec db dropdb -U $(DB_USER) --if-exists $(db)

install: ## Install a module into the target db:  make install m=<module> [db=...]
	@test -n "$(m)" || (echo "Usage: make install m=<module> [db=$(db)]"; exit 1)
	$(COMPOSE) exec odoo odoo -d $(db) -i $(m) --stop-after-init $(ODOO_DB_ARGS)
	$(COMPOSE) restart odoo

upgrade: ## Upgrade a module after editing it:  make upgrade m=<module> [db=...]
	@test -n "$(m)" || (echo "Usage: make upgrade m=<module> [db=$(db)]"; exit 1)
	$(COMPOSE) exec odoo odoo -d $(db) -u $(m) --stop-after-init $(ODOO_DB_ARGS)
	$(COMPOSE) restart odoo

## ---- Demo (separate React prototype, NOT the Odoo product) ----
demo: ## Run the standalone React demo UI on :5173
	cd demo && npm install && npm run dev

## ---- Routing verification (P1-T06, opt-in — does NOT change `make up`) ----
routing-up: ## Start the routing stack (db_filter=^%d$ ON) to prove subdomain->DB routing
	$(ROUTING_COMPOSE) up -d

routing-verify: ## Create clienta/clientb/admin test DBs and run the isolation proof
	./scripts/routing/verify_routing.sh

routing-down: ## Stop the routing stack (keeps the test DBs; back to a normal `make up`)
	$(ROUTING_COMPOSE) down

routing-clean: ## Drop the clienta/clientb/admin test databases (destructive)
	@for d in clienta clientb admin; do $(COMPOSE) exec db dropdb -U $(DB_USER) --if-exists $$d; done
