# ============================================================================
#  NCollection ERP — developer command shortcuts
# ============================================================================
#  Thin wrappers over `docker compose`. Run `make help` for the list.
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

.DEFAULT_GOAL := help
.PHONY: help up down stop restart logs ps shell psql odoo-shell \
        bootstrap createdb dropdb install upgrade demo

help: ## Show this help
	@echo "NCollection ERP — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: db=<database> (default: $(db)), m=<module>"

## ---- Stack lifecycle ----
up: ## Start the stack in the background (Odoo on :8069)
	docker compose up -d

down: ## Stop and remove containers (keeps data volumes)
	docker compose down

stop: ## Stop containers without removing them
	docker compose stop

restart: ## Restart just the Odoo container (apply mounted code/config)
	docker compose restart odoo

logs: ## Follow Odoo logs (Ctrl-C to exit)
	docker compose logs -f odoo

ps: ## Show container status
	docker compose ps

## ---- Shells ----
shell: ## Open a bash shell inside the Odoo container
	docker compose exec odoo bash

psql: ## Open a psql prompt on the target database (db=...)
	docker compose exec db psql -U $(DB_USER) -d $(db)

odoo-shell: ## Open an Odoo Python shell against the target database (db=...)
	docker compose exec odoo odoo shell -d $(db) $(ODOO_DB_ARGS)

## ---- Database & modules ----
bootstrap: ## Create db=... and install the NCollection modules (first-run setup)
	docker compose exec odoo odoo -d $(db) \
		-i base,ncollection_subscription,ncollection_branding \
		--stop-after-init $(ODOO_DB_ARGS)
	docker compose restart odoo
	@echo "Done. Open http://localhost:8069  (select database '$(db)')"

createdb: ## Create and initialize an empty Odoo database (db=...)
	docker compose exec odoo odoo -d $(db) -i base --stop-after-init $(ODOO_DB_ARGS)
	docker compose restart odoo

dropdb: ## Drop the target database (db=...) — destructive
	docker compose exec db dropdb -U $(DB_USER) --if-exists $(db)

install: ## Install a module into the target db:  make install m=<module> [db=...]
	@test -n "$(m)" || (echo "Usage: make install m=<module> [db=$(db)]"; exit 1)
	docker compose exec odoo odoo -d $(db) -i $(m) --stop-after-init $(ODOO_DB_ARGS)
	docker compose restart odoo

upgrade: ## Upgrade a module after editing it:  make upgrade m=<module> [db=...]
	@test -n "$(m)" || (echo "Usage: make upgrade m=<module> [db=$(db)]"; exit 1)
	docker compose exec odoo odoo -d $(db) -u $(m) --stop-after-init $(ODOO_DB_ARGS)
	docker compose restart odoo

## ---- Demo (separate React prototype, NOT the Odoo product) ----
demo: ## Run the standalone React demo UI on :5173
	cd demo && npm install && npm run dev
