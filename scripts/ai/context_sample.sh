#!/usr/bin/env bash
# ============================================================================
#  #60 / P5-T03 — context-quality review harness
# ============================================================================
#  The ticket's second acceptance criterion is "context quality reviewed on 20
#  sample questions". That is a HUMAN judgement — whether the assembled context
#  would actually let a model answer — so it cannot be asserted in a test and
#  this script does not pretend to.
#
#  What it automates is the REVIEW: it builds the real context against a real
#  tenant database and prints, per question, what the model would receive. The
#  judgement is then a thing you can make in one sitting instead of
#  reconstructing by hand.
#
#  The question set (custom_addons/ncollection_ai/data/sample_questions.json)
#  deliberately spans failure modes rather than flattering the builder:
#  10 answerable, 5 partial, 4 unanswerable, 1 security check. A context that
#  quietly lacks the data is worse than one that visibly does.
#
#  Usage:  make ai-context-sample [db=albarari]
#          db defaults to `albarari`, the populated demo tenant — a review
#          against an empty database tells you nothing.
# ============================================================================
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

DB="${1:-albarari}"
DC=(docker compose -f docker-compose.yml -f docker-compose.dev.yml)

if ! "${DC[@]}" exec -T db psql -U odoo -d postgres -tAc \
     "SELECT 1 FROM pg_database WHERE datname='$DB'" 2>/dev/null | grep -q 1; then
  echo "ERROR: database '$DB' does not exist." >&2
  echo "       Build the populated demo tenant first:  make demo-tenant" >&2
  exit 1
fi

if ! "${DC[@]}" exec -T db psql -U odoo -d "$DB" -tAc \
     "SELECT 1 FROM ir_module_module WHERE name='ncollection_ai' AND state='installed'" \
     2>/dev/null | grep -q 1; then
  echo "ERROR: ncollection_ai is not installed on '$DB'." >&2
  echo "       Install it:  make install m=ncollection_ai db=$DB" >&2
  exit 1
fi

echo "======================================================================"
echo " Context-quality review — database: $DB"
echo " Acceptance: 'context quality reviewed on 20 sample questions'"
echo " This prints what the model WOULD receive. The verdict is yours."
echo "======================================================================"

"${DC[@]}" exec -T odoo odoo shell -d "$DB" --no-http --log-level=error \
  --db_host=db --db_user=odoo --db_password=odoo 2>/dev/null <<'PY'
import json

with open('/mnt/extra-addons/ncollection_ai/data/sample_questions.json') as fh:
    questions = json.load(fh)['questions']

Context = env['ncollection.ai.context']
Pii = env['ncollection.ai.pii']

context = Context.build()
clean, mapping = Pii.sanitise({'context': context['sections']})

print()
print("SECTIONS PRESENT : %s" % ", ".join(sorted(clean['context'])))
print("DROPPED          : %s" % (context['dropped'] or "none"))
print("ESTIMATED TOKENS : %s" % context['estimated_tokens'])
print("PSEUDONYMS       : %d identity token(s) — real values never leave this DB"
      % len(mapping))
print()
print("---------------- CONTEXT AS THE MODEL WOULD SEE IT ----------------")
print(json.dumps(clean['context'], indent=2, default=str)[:4000])
print("-------------------------------------------------------------------")
print()

buckets = {}
for item in questions:
    buckets.setdefault(item['expect'], []).append(item)

for expect in ('answerable', 'partial', 'unanswerable', 'refused'):
    items = buckets.get(expect, [])
    if not items:
        continue
    print("== %s (%d) ==" % (expect.upper(), len(items)))
    for item in items:
        needs = item.get('needs', '')
        missing = [s for s in needs.split(',') if s and s not in clean['context']]
        if expect == 'answerable':
            verdict = "context HAS what it needs" if not missing \
                      else "MISSING: %s" % ", ".join(missing)
        else:
            verdict = item.get('why', '')
        print("  - %-62s %s" % (item['q'][:62], verdict))
    print()

print("Review each ANSWERABLE row: does the context above genuinely support it?")
print("A 'context HAS what it needs' line means the SECTION is present, not that")
print("its contents are sufficient — that judgement is the point of this review.")
PY
