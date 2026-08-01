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

# OCA addon repos (P1-T04): ./oca/ is GENERATED from the pins in repos.yml —
# run `make oca` after a fresh clone or whenever repos.yml changes.
OCA_VENV := .oca-venv

.DEFAULT_GOAL := help
.PHONY: help up down stop restart logs ps shell psql odoo-shell \
        bootstrap createdb dropdb install upgrade demo oca \
        routing-up routing-verify routing-down routing-clean e2e-clean \
        load-test load-test-clean security-assess \
        provisioning-verify config-sync-verify financial-bootstrap-verify e2e-verify verify-all hooks-install doctor \
        demo-tenant demo-clean staging-config staging-build go-live-check

help: ## Show this help
	@echo "NCollection ERP — make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: db=<database> (default: $(db)), m=<module>"

## ---- Stack lifecycle ----
up: ## Start the dev stack (Odoo :8069, Nginx edge :80, pgAdmin :5050)
	@test -d oca/mis-builder || (echo "ERROR: ./oca/ is empty — run 'make oca' first (aggregates the pinned OCA repos from repos.yml)."; exit 1)
	$(COMPOSE) up -d

oca: ## Aggregate the pinned OCA addon repos from repos.yml into ./oca/
	@test -x $(OCA_VENV)/bin/gitaggregate || \
		(python3 -m venv $(OCA_VENV) && $(OCA_VENV)/bin/pip -q install 'git-aggregator==4.1')
	$(OCA_VENV)/bin/gitaggregate -c repos.yml -j 3
	@echo "OCA repos aggregated. Apply with: make restart"

## ---- Staging / CD (P2-T07) ----
staging-config: ## Validate the merged staging compose config (docker-compose.yml + .staging.yml)
	docker compose -f docker-compose.yml -f docker-compose.staging.yml config -q && echo "✅ staging compose valid"

staging-build: ## Build the deployable image locally (run 'make oca' first). Tag: :local
	@test -d oca/mis-builder || (echo "ERROR: ./oca/ is empty — run 'make oca' first."; exit 1)
	docker build -t ghcr.io/ncollection-sys/ncollection-erp:local .

go-live-check: ## P3-T13 go-live readiness preflight (verifies automated gate items; lists manual ones)
	./scripts/deploy/go_live_check.sh

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

# Odoo keeps pooled connections open, so a bare `dropdb` fails with
# "database is being accessed by other users" — which made every *-clean target
# silently do nothing. Terminate live sessions first, exactly as the
# drop_db() helpers in our verification scripts already do.
define drop_database
$(COMPOSE) exec -T db psql -U $(DB_USER) -d postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='$(1)'" >/dev/null 2>&1 || true; \
$(COMPOSE) exec -T db dropdb -U $(DB_USER) --if-exists $(1)
endef

dropdb: ## Drop the target database (db=...) — destructive
	@$(call drop_database,$(db))

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

routing-verify: ## Create rtclienta/rtclientb/rtadmin test DBs and run the isolation proof
	./scripts/routing/verify_routing.sh

routing-down: ## Stop the routing stack (keeps the test DBs; back to a normal `make up`)
	$(ROUTING_COMPOSE) down

# FIXTURE NAMESPACES — each suite owns its own DB prefix and may only drop its
# own. routing: rt* · e2e: e2e* · provisioning: prov*.
# They used to share one namespace, so either suite could destroy the other's
# fixtures; the split makes that structurally impossible.
routing-clean: ## Drop the ROUTING fixture DBs rtclienta/rtclientb/rtadmin (destructive)
	@for d in rtclienta rtclientb rtadmin; do $(call drop_database,$$d); done

e2e-clean: ## Drop the E2E fixture DBs e2eclienta/e2eclientb/e2eadmin (destructive)
	@for d in e2eclienta e2eclientb e2eadmin; do $(call drop_database,$$d); done

security-assess: ## Run the P3-T12 pre-launch security assessment (needs routing-up) — see docs/markdown/SECURITY_ASSESSMENT_P3-T12.md
	./scripts/audit/phase3_security_assessment.sh

load-test: ## Run the P3-T03 k6 load test (sweep VUs across loadtesta/b/c) — see docs/perf/
	./scripts/perf/run_load_test.sh

load-test-clean: ## Drop the LOAD-TEST fixture DBs loadtesta/loadtestb/loadtestc (destructive)
	@for d in loadtesta loadtestb loadtestc; do $(call drop_database,$$d); done

## ---- Cross-suite verification --------------------------------------------
# A ticket that only proves its OWN lane cannot see a cross-suite regression.
# `verify-all` runs every guarantee we have against one running stack, and is
# the gate to run before merging ANY change — not just the lane you touched.
# Requires the routing stack up: `make routing-up`.
provisioning-verify: ## Run the P2-T01 provisioning proof (create -> login-ready, forced-failure -> rollback)
	./custom_addons/ncollection_saas/scripts/provisioning/verify_provisioning.sh

config-sync-verify: ## Run the P2-T03 config-sync proof (provision -> plan change -> suspend -> reconcile)
	./custom_addons/ncollection_saas/scripts/provisioning/verify_config_sync.sh

financial-bootstrap-verify: ## Run the P3-T01 proof (Enterprise financial set installs -> Trial Balance runs on UAE data)
	./custom_addons/ncollection_saas/scripts/provisioning/verify_financial_bootstrap.sh

e2e-verify: ## Set up the e2e tenants and run the Playwright suite
	bash e2e/scripts/setup_e2e_tenants.sh
	cd e2e && npm ci && npx playwright install chromium && npx playwright test

## ---- Demo tenant -----------------------------------------------------------
# A populated workspace to actually look at. Provisioned THROUGH the P2-T01
# engine (same path a real signup takes), then seeded with curated GCC data.
# The engine is never modified: --without-demo=True still holds, so no paying
# customer can receive Odoo demo data. Requires `make routing-up`.
demo-tenant: ## Build/refresh the populated demo tenant (REBUILD=1 to start clean)
	@bash scripts/demo/build_demo_tenant.sh

demo-clean: ## Drop the demo tenant + platform DB (destructive)
	@for d in albarari ncplatform; do $(call drop_database,$$d); done

## ---- Developer environment -------------------------------------------------
hooks-install: ## Enable the repo's git hooks (fast gates run on pre-push)
	@git config core.hooksPath .githooks
	@chmod +x .githooks/* 2>/dev/null || true
	@echo "✅ hooks enabled (core.hooksPath=.githooks). Bypass once with: git push --no-verify"

doctor: ## Diagnose the local dev environment ("why doesn't this work on my machine?")
	@bash scripts/dev/doctor.sh

verify-all: ## Run EVERY verification suite (routing + provisioning + config-sync + financial-bootstrap + e2e) — pre-merge gate
	@echo "==> [1/5] routing & isolation (P1-T06)"
	@$(MAKE) --no-print-directory routing-verify
	@echo "==> [2/5] provisioning (P2-T01)"
	@$(MAKE) --no-print-directory provisioning-verify
	@echo "==> [3/5] config sync (P2-T03)"
	@$(MAKE) --no-print-directory config-sync-verify
	@echo "==> [4/5] financial bootstrap (P3-T01)"
	@$(MAKE) --no-print-directory financial-bootstrap-verify
	@echo "==> [5/5] end-to-end guarantees (P1-T20)"
	@$(MAKE) --no-print-directory e2e-verify
	@echo "✅ verify-all: every suite green."
