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

# The FULL SaaS development stack (#463): the routing stack PLUS the
# provisioning-runner satellite from docker-compose.saas.yml, which runs OCA
# queue_job's jobrunner. Without that container the platform still ENQUEUES
# work correctly and nothing ever drains it — module installs (#461), config
# sync and provisioning all sit `pending`, so the SaaS lifecycle cannot be
# exercised at all. `saas-up` used to be a thin alias over ROUTING_COMPOSE and
# inherited exactly that gap.
#
# One overlay per concern, layered — no duplicated flags: base (services) +
# dev (dev command/nginx) + routing (db_filter/list_db/proxy_mode) + saas
# (the runner). docker-compose.saas.yml defines the runner ONCE and is shared
# with the prod stack.
SAAS_COMPOSE ?= $(ROUTING_COMPOSE) -f docker-compose.saas.yml

# The platform/admin database, i.e. the one carrying ncollection_saas. On the
# SaaS-routing stack the HOSTNAME is the database (db_filter=^%d$), so this is
# also the platform's subdomain: http://$(NC_PLATFORM_DB).localhost/ (#453).
# Override per environment: `make saas-urls NC_PLATFORM_DB=ncplatform`.
NC_PLATFORM_DB ?= ncollection

# Opt-in cron-starvation harness stack (#310): base + the cronstall overlay. Its
# services are entirely self-contained (own Postgres, own network, own volumes),
# so this does NOT affect `make up` and the harness cannot disturb the dev stack.
CRONSTALL_COMPOSE ?= docker compose -f docker-compose.yml -f docker-compose.cronstall.yml
# Opt-in cron-SCOPE harness stack (#343): base + the cronscope overlay. Its own
# Postgres, for the same reason — the RED arm deliberately runs other databases'
# crons, which must never happen on the shared dev db (Rule 14).
CRONSCOPE_COMPOSE ?= docker compose -f docker-compose.yml -f docker-compose.cronscope.yml

# OCA addon repos (P1-T04): ./oca/ is GENERATED from the pins in repos.yml —
# run `make oca` after a fresh clone or whenever repos.yml changes.
OCA_VENV := .oca-venv

.DEFAULT_GOAL := help
.PHONY: help up down stop restart logs ps shell psql odoo-shell \
        bootstrap createdb dropdb install upgrade demo oca \
        routing-up routing-verify routing-down routing-clean e2e-clean \
        saas-up saas-down saas-urls saas-jobs saas-runner-logs \
        load-test load-test-clean security-assess \
        provisioning-verify config-sync-verify financial-bootstrap-verify e2e-verify verify-all hooks-install doctor \
        cron-starvation-verify cron-starvation-clean orphan-dbs \
        cron-scope-verify cron-scope-clean \
        upgrade-verify upgrade-clean \
        ai-up ai-down ai-logs ai-test ai-verify ai-context-sample \
        demo-tenant demo-clean staging-config staging-build go-live-check stack-settled

# `grep -h` is load-bearing (#338). `-include .env` puts a SECOND file in
# MAKEFILE_LIST, and grep prefixes every match with `filename:` once it is given
# more than one file. The awk FS then splits at that first colon, so `$1` came
# out as "Makefile" for every row instead of the target name — on every machine
# that has a .env, i.e. every configured dev machine. It looked fine on a fresh
# clone, which is how it survived this long.
help: ## Show this help
	@echo "NCollection ERP — make targets:"
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-26s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  Variables: db=<database> (default: $(db)), m=<module>"

## ---- Stack lifecycle ----
up: ## Start the dev stack (Odoo :8069, Nginx edge :80, pgAdmin :5050)
	@./scripts/dev/assert_oca_present.sh "the dev stack"
	$(COMPOSE) up -d

oca: ## Aggregate the pinned OCA addon repos from repos.yml into ./oca/
	@test -x $(OCA_VENV)/bin/gitaggregate || \
		(python3 -m venv $(OCA_VENV) && $(OCA_VENV)/bin/pip -q install 'git-aggregator==4.1')
	$(OCA_VENV)/bin/gitaggregate -c repos.yml -j 3
	@echo "OCA repos aggregated. Apply with:"
	@echo "  docker compose up -d --force-recreate odoo"
	@echo ""
	@echo "NOT 'make restart' (#427): restarting reuses the mount"
	@echo "namespace, so a config file replaced by rename stays stale and"
	@echo "Odoo silently falls back to defaults. 'up -d' does not re-bind"
	@echo "either — it reports Healthy and changes nothing."

## ---- Staging / CD (P2-T07) ----
staging-config: ## Validate the merged staging compose config (docker-compose.yml + .staging.yml)
	docker compose -f docker-compose.yml -f docker-compose.staging.yml config -q && echo "✅ staging compose valid"

staging-build: ## Build the deployable image locally (run 'make oca' first). Tag: :local
	@./scripts/dev/assert_oca_present.sh "the staging image build"
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

## ---- Tests ----
# The local counterpart to CI's `test` job (#365). Until this target existed the
# 869 tests had NEVER been run locally: they only ever executed inside CI's
# disposable container, which hands them a private Postgres and a free port 8069.
# Against the live dev stack both assumptions break, and two flags are therefore
# NOT optional — omit either and the suite dies in ~1s having run nothing:
#
#   * ODOO_DB_ARGS        the container's /etc/odoo/odoo.conf carries no DB
#                         credentials, so a bare `exec odoo odoo` resolves
#                         default@default:default and fails on connect.
#   * --http-port/--gevent-port
#                         --test-enable starts the HTTP server so the HttpCase
#                         classes have something to talk to, and the container is
#                         ALREADY serving on 8069 -> "Address already in use".
#
# The module list is DERIVED from ci.yml (scripts/dev/ci_matrix.py) rather than
# copied here, so "local runs what CI runs" holds by construction. A second copy
# would drift exactly as the pylint gate did before #267.
TEST_DB          ?= nctest
TEST_HTTP_PORT   ?= 8169
TEST_GEVENT_PORT ?= 8172

test: ## Run the Odoo test suite locally, same matrix as CI (m=<module> to scope) — owns db 'nctest'
	@python3 scripts/dev/ci_matrix.py --self-test
	@set -e; \
	if [ -n "$(m)" ]; then mods="$(m)"; tags="/$(m)"; \
	else mods="$$(python3 scripts/dev/ci_matrix.py --modules)"; \
	     tags="$$(python3 scripts/dev/ci_matrix.py --tags)"; fi; \
	echo "==> db     : $(TEST_DB)"; \
	echo "==> modules: $$mods"; \
	echo "==> tags   : $$tags"; \
	$(call drop_database,$(TEST_DB)); \
	log="$$(mktemp)"; rcfile="$$(mktemp)"; \
	( $(COMPOSE) exec -T odoo odoo -d $(TEST_DB) $(ODOO_DB_ARGS) \
	      --http-port=$(TEST_HTTP_PORT) --gevent-port=$(TEST_GEVENT_PORT) \
	      -i "$$mods" --test-enable --test-tags "$$tags" \
	      --stop-after-init --log-level=test 2>&1; \
	  echo $$? > "$$rcfile" ) | tee "$$log"; \
	rc="$$(cat "$$rcfile")"; \
	$(call drop_database,$(TEST_DB)); \
	if [ "$$rc" -ne 0 ]; then \
	  echo "FAILED: odoo exited $$rc"; rm -f "$$log" "$$rcfile"; exit 1; fi; \
	if grep -qi "traceback (most recent call last)" "$$log"; then \
	  echo "FAILED: traceback in the test log (odoo can exit 0 on a failed test —"; \
	  echo "        this is the same grep ci.yml applies for that reason)."; \
	  rm -f "$$log" "$$rcfile"; exit 1; fi; \
	if ! grep -q "odoo.tests.result:" "$$log"; then \
	  echo "FAILED: no 'odoo.tests.result' line — the suite did not RUN."; \
	  echo "        Silence is not success: a log with no result line and no"; \
	  echo "        traceback is what odoo leaves when it dies before testing"; \
	  echo "        (bad --db_* args, port already bound) or when --test-tags"; \
	  echo "        matched nothing. Both would otherwise report green."; \
	  rm -f "$$log" "$$rcfile"; exit 1; fi; \
	if ! grep -q "odoo.tests.result: 0 failed, 0 error(s)" "$$log"; then \
	  echo "FAILED: odoo reported failing tests:"; \
	  grep "odoo.tests.result:" "$$log"; rm -f "$$log" "$$rcfile"; exit 1; fi; \
	grep "odoo.tests.result:" "$$log" || true; \
	rm -f "$$log" "$$rcfile"; \
	echo "OK: local suite green (db $(TEST_DB) dropped)."

## ---- Upgrade proof (#362) ----
# The `-u` path had NO coverage: deploy.sh runs no upgrade at all (it deploys the
# image then curls /web/health — liveness, not correctness) and CI always installs
# fresh with -i. Yet five modules ship eight migration scripts that run once,
# against live customer data, on a database-per-tenant platform.
upgrade-verify: ## Prove module upgrades run their migrations and data survives (#362) — owns upgr*
	./scripts/upgrade/verify_upgrade.sh

upgrade-clean: ## Drop the UPGRADE fixture DBs upgrgreen/upgrred/upgrcore (destructive)
	@for d in upgrgreen upgrred upgrcore; do $(call drop_database,$$d); done

## ---- AI gateway satellite (P5-T02 / #59, opt-in) ----
# The platform's FIRST satellite (ARCHITECTURE_DATA_PLATFORM §10.2) and the only
# component allowed to call an LLM provider (§11). Deliberately an OVERLAY, not
# part of `make up`: a service in the base compose sits in the path of every
# suite — routing, e2e, provisioning, both cron harnesses, financial bootstrap,
# upgrade — for a feature none of them exercise.
AI_COMPOSE ?= $(COMPOSE) -f docker-compose.ai.yml

ai-up: ## Start the AI gateway satellite (mock provider by default)
	$(AI_COMPOSE) up -d ai-gateway

ai-down: ## Stop the AI gateway satellite
	$(AI_COMPOSE) stop ai-gateway

ai-logs: ## Follow the AI gateway logs (structured JSON, metadata only)
	$(AI_COMPOSE) logs -f ai-gateway

ai-test: ## Run the satellite's own unit + HTTP tests (no Docker, no network)
	python3 satellites/ai_gateway/test_ai_gateway.py
	python3 satellites/ai_gateway/test_gateway_http.py

ai-verify: ## Prove the gateway is a choke point and cannot reach a database (#59)
	./scripts/ai/verify_ai_gateway.sh

ai-context-sample: ## Print the AI context for the 20 review questions (#60) [db=albarari]
	./scripts/ai/context_sample.sh $(db)

## ---- Demo (separate React prototype, NOT the Odoo product) ----
demo: ## Run the standalone React demo UI on :5173
	cd demo && npm install && npm run dev

## ---- Routing verification (P1-T06, opt-in — does NOT change `make up`) ----
routing-up: ## Start the routing stack (db_filter=^%d$ ON) to prove subdomain->DB routing
	@./scripts/dev/assert_oca_present.sh "the routing stack"
	$(ROUTING_COMPOSE) up -d

routing-verify: ## Create rtclienta/rtclientb/rtadmin test DBs and run the isolation proof
	./scripts/routing/verify_routing.sh

routing-down: ## Stop the routing stack (keeps the test DBs; back to a normal `make up`)
	$(ROUTING_COMPOSE) down

## ---- SaaS development / demo stack (#453, #463) ----------------------------
# THE command for realistic SaaS work: routing (subdomain -> database, selector
# off) PLUS the queue worker that actually drains the jobs the platform
# enqueues. `make up` stays the lightweight permissive stack -- see
# docs/markdown/ROUTING.md for which mode does what.
#
# Platform and tenants are told apart ONLY by hostname, exactly as production
# does it with db_filter=^%d$.
saas-up: ## Start the FULL SaaS dev stack (routing + queue worker) — use this for SaaS work
	@./scripts/dev/assert_oca_present.sh "the SaaS stack"
	$(SAAS_COMPOSE) up -d
	@$(MAKE) --no-print-directory saas-urls

saas-down: ## Stop the SaaS stack (back to the permissive `make up` dev stack)
	$(SAAS_COMPOSE) down

# Job observability (#463). A stack whose worker is up but silently failing
# looks identical to one that is working until someone asks a tenant.
saas-jobs: ## List queue jobs on the platform DB with their state (queued/started/done/failed)
	@echo "== queue_job on $(NC_PLATFORM_DB) =="
	@$(COMPOSE) exec -T db psql -U $(DB_USER) -d $(NC_PLATFORM_DB) -c \
		"SELECT id, state, channel, method_name, identity_key, \
		        date_created, exc_message \
		   FROM queue_job ORDER BY id DESC LIMIT 30;" \
	 || echo "  (no queue_job table — is ncollection_saas installed on $(NC_PLATFORM_DB)?)"
	@echo "== tenant-side lifecycle state =="
	@$(COMPOSE) exec -T db psql -U $(DB_USER) -d $(NC_PLATFORM_DB) -c \
		"SELECT database_name, database_status, module_install_state, \
		        config_sync_state, module_install_last_error \
		   FROM ncollection_tenant ORDER BY id;" || true

saas-runner-logs: ## Follow the queue worker's logs (the container that runs the jobs)
	$(SAAS_COMPOSE) logs -f provisioning-runner

saas-urls: ## Print the platform + tenant entry points for the SaaS-routing stack
	@echo "SaaS-routing entry points (db_filter=^%d$$ — the hostname IS the database):"
	@echo "  platform admin : http://$(NC_PLATFORM_DB).localhost/       (db: $(NC_PLATFORM_DB))"
	@echo "  a tenant       : http://<tenant-db>.localhost/     e.g. http://wasla.localhost/"
	@echo "  bare localhost : redirects to the platform host (nginx, dev only)"
	@echo "  database selector: disabled (list_db=False) and 403'd at the edge"
	@echo "Background jobs ARE processed on this stack (provisioning-runner):"
	@echo "  inspect : make saas-jobs        (states: pending/enqueued/started/done/failed)"
	@echo "  logs    : make saas-runner-logs"
	@echo "Back to everyday dev (selector on, no db_filter, NO worker):  make saas-down && make up"

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

agg-bench: ## Run the P4-T01 aggregation query-budget benchmark (100k rows, <500ms) — rows=AGG_BENCH_ROWS
	./scripts/perf/run_aggregation_bench.sh

agg-clean: ## Drop the AGGREGATION-BENCH fixture DB aggbench (destructive)
	@for d in aggbench; do $(call drop_database,$$d); done

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

cron-scope-verify: ## Run the #343 proof (provisioning-runner ticks the platform DB's crons only, and still ticks them)
	./custom_addons/ncollection_saas/scripts/provisioning/verify_cron_scope.sh

# Same reasoning as cron-starvation-clean below: this fixture is NOT on the
# shared db, so there is nothing to drop_database — removing the private
# volumes is what resets it. Project name derived, never hardcoded (Rule 11).
cron-scope-clean: ## Remove the CRON-SCOPE harness stack + its private volumes (destructive)
	@$(CRONSCOPE_COMPOSE) rm -sf cron-scope-runner cron-scope-db >/dev/null
	@proj=$$($(CRONSCOPE_COMPOSE) config --format json \
		| python3 -c "import json,sys; print(json.load(sys.stdin)['name'])"); \
	for v in cronscope_pgdata cronscope_data; do \
		docker volume rm -f "$${proj}_$${v}" >/dev/null; \
	done
	@echo "✅ cron-scope harness stack + volumes removed."

cron-starvation-verify: ## Run the #310 proof (a stalled outbound fetch must not delay the config-sync reconcile cron)
	@./scripts/dev/assert_oca_present.sh "the cron-starvation harness"
	./custom_addons/ncollection_saas/scripts/provisioning/verify_cron_starvation.sh

# NOT a `drop_database` call: this suite's fixture does NOT live on the shared
# db. It has its own Postgres (docker-compose.cronstall.yml) precisely so no
# other Odoo can run its crons — see DESIGN_CRON_AND_QUEUE_TOPOLOGY.md §5.1.
# Removing the volumes is what resets it.
#
# The project name is DERIVED from compose, never hardcoded: a non-default
# COMPOSE_PROJECT_NAME renames every volume, and a hardcoded `ncollection-erp_*`
# would silently clean nothing (Rule 11 / R-006). `docker volume rm -f` already
# ignores a missing volume, so no `|| true` is needed — and none is wanted: if a
# volume is still in use, that must fail loudly rather than print success over a
# stale fixture (Rule 10 / R-005).
cron-starvation-clean: ## Remove the CRON-STARVATION harness stack + its private volumes (destructive)
	@$(CRONSTALL_COMPOSE) rm -sf cron-stall-host cron-stall-db cron-stall-odoo >/dev/null
	@proj=$$($(CRONSTALL_COMPOSE) config --format json \
		| python3 -c "import json,sys; print(json.load(sys.stdin)['name'])"); \
	for v in cronstall_pgdata cronstall_data; do \
		docker volume rm -f "$${proj}_$${v}" >/dev/null; \
	done
	@echo "✅ cron-starvation harness stack + volumes removed."

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

stack-settled: ## Was db/odoo just (re)started? Sanity check before trusting a scary finding (R-018)
	@bash scripts/dev/stack_settled.sh

# READ-ONLY on purpose (#337). It lists; it never drops. Every name here is
# someone's fixture until proven otherwise, and the fixture-ownership table in
# CLAUDE.md is the authority on which suite owns what — dropping the wrong one
# silently destroys another suite's setup, which is REGRESSIONS.md R-004.
# Drop what you recognise, deliberately:  make dropdb db=<name>
orphan-dbs: ## List databases owned by no documented suite (read-only; never drops)
	@bash scripts/dev/orphan_dbs.sh

verify-all: ## Run EVERY verification suite (routing + provisioning + config-sync + cron + financial-bootstrap + upgrade + e2e) — pre-merge gate
	@./scripts/dev/assert_oca_present.sh "verify-all"
	@echo "==> [1/8] routing & isolation (P1-T06)"
	@$(MAKE) --no-print-directory routing-verify
	@echo "==> [2/8] provisioning (P2-T01)"
	@$(MAKE) --no-print-directory provisioning-verify
	@echo "==> [3/8] config sync (P2-T03)"
	@$(MAKE) --no-print-directory config-sync-verify
	@echo "==> [4/8] cron starvation (#310)"
	@$(MAKE) --no-print-directory cron-starvation-verify
	@echo "==> [5/8] cron scope (#343)"
	@$(MAKE) --no-print-directory cron-scope-verify
	@echo "==> [6/8] financial bootstrap (P3-T01)"
	@$(MAKE) --no-print-directory financial-bootstrap-verify
	@echo "==> [7/8] module upgrade path (#362)"
	@$(MAKE) --no-print-directory upgrade-verify
	@echo "==> [8/8] end-to-end guarantees (P1-T20)"
	@$(MAKE) --no-print-directory e2e-verify
	@echo "✅ verify-all: every suite green."
