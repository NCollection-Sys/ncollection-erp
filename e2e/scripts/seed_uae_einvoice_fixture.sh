#!/usr/bin/env bash
# ============================================================================
#  UAE e-invoicing FUNCTIONAL fixture: the master data an invoice needs on `e2euae`, nothing else.
# ============================================================================
#  setup_e2e_uae_tenant.sh provisions the tenant (licence, localization, access). This script is separate and invoked
#  explicitly: it seeds what the engine requires before Prepare / Generate XML can pass (lib/readiness.py), using the
#  SAME values as the module's own server tests (ncollection_uae_einvoicing/tests/common.py):
#    - the company partner: street / city / emirate DU / AE, TIN, TRN, trade-licence registration + authority;
#    - one buyer (UAE business, emirate AZ) with TIN, TRN, trade-licence registration;
#    - one Goods product with an HS code (unit = Units -> UN/ECE C62, lib/units.py);
#    - the tax map: '5% DU' -> S, '5% AZ' -> S, '0% EXT' -> E (DL8.46.1), '0%' -> Z (the AE chart's own taxes, never
#      recreated). '5% AZ' because the buyer is in Abu Dhabi: l10n_ae auto-applies the Abu Dhabi fiscal position,
#      which replaces the company default '5% DU' with '5% AZ' on every line entered through the invoice form;
#    - the engine config: enabled, default payment means 10 (in the pinned UNCL4461 list, lib/payment_means.py).
#  ALL identifiers are FICTIONAL test data. They pass the format checks only (15 / 10 digits; the FTA publishes no
#  checksum) and are not registrations of any real entity. No invoice, no provider, no credential, no ASP.
#
#  Safety: refuses unless the database is exactly e2euae, carries this fixture's ownership marker, is registered under
#  the same marker in the e2eadmin platform, is the single company 'E2E UAE CO' on ae/AED, has the engine installed,
#  the A-Cube adapter NOT installed and no provider or secret. Every seeded record gets a stable external id under
#  `ncollection_e2e_fixture.`; a record that already exists WITHOUT that id, or with values other than the fixture's,
#  is a conflict and the run stops — nothing is overwritten. Empty company identity fields are filled; a different
#  value already there is a conflict too.
#  Idempotent: a second run finds every record by its external id, compares, and writes nothing.
#  Atomic: everything happens in one transaction, committed only after every check passed.
#  `--check` runs the same code and rolls back (proves what a run would do, writes nothing).
#  No cleanup: the records are removed with the tenant (make e2e-uae-clean).
# ============================================================================
set -euo pipefail

cd "$(dirname "$0")/../.."   # repo root

PLATFORM_DB=e2eadmin
TENANT_DB=e2euae
FIXTURE_MARK="ncollection-e2e-fixture:${TENANT_DB}@${PLATFORM_DB}"
COMPANY_NAME='E2E UAE CO'
MODE=apply
case "${1:-}" in
  "") ;;
  --check) MODE=check ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

DC=(docker compose)
DBARGS=(--db_host=db --db_user=odoo --db_password=odoo)

psql_db(){ "${DC[@]}" exec -T db psql -U odoo -d "$1" -tAc "$2"; }
refuse(){ echo "❌ REFUSING: $*" >&2; exit 1; }

echo "== preflight ($MODE) =="
[ "$(psql_db postgres "SELECT 1 FROM pg_database WHERE datname='$TENANT_DB'")" = 1 ] \
  || refuse "database '$TENANT_DB' does not exist (run make e2e-uae-setup)"
[ "$(psql_db "$TENANT_DB" "SELECT coalesce(max(value),'') FROM ir_config_parameter WHERE key='ncollection.e2e.fixture'")" \
  = "$FIXTURE_MARK" ] || refuse "'$TENANT_DB' does not carry the ownership marker $FIXTURE_MARK"
[ "$(psql_db "$PLATFORM_DB" "SELECT count(*) FROM ncollection_tenant WHERE database_name='$TENANT_DB' AND subdomain='$TENANT_DB' AND contact_name='$FIXTURE_MARK'")" = 1 ] \
  || refuse "'$PLATFORM_DB' does not register '$TENANT_DB' under the fixture marker"
[ "$(psql_db "$TENANT_DB" "SELECT state FROM ir_module_module WHERE name='ncollection_uae_einvoicing'")" = installed ] \
  || refuse "ncollection_uae_einvoicing is not installed on '$TENANT_DB'"
[ "$(psql_db "$TENANT_DB" "SELECT count(*) FROM ir_module_module WHERE name LIKE 'ncollection_uae_einvoicing\_%' AND state <> 'uninstalled'")" = 0 ] \
  || refuse "an ASP adapter module is installed (or being installed) on '$TENANT_DB'"
echo "  marker, platform registration and modules verified"

if ! out=$("${DC[@]}" exec -T -e NC_E2E_MARK="$FIXTURE_MARK" -e NC_E2E_COMPANY="$COMPANY_NAME" -e NC_E2E_MODE="$MODE" \
             odoo odoo shell -d "$TENANT_DB" --no-http --log-level=warn "${DBARGS[@]}" 2>&1 <<'PY'
import os

MOD = 'ncollection_e2e_fixture'
mode = os.environ['NC_E2E_MODE']


def refuse(msg):
    env.cr.rollback()
    raise SystemExit('REFUSE: ' + msg)


# ---- identity, re-checked inside the transaction that writes
if env['ir.config_parameter'].sudo().get_param('ncollection.e2e.fixture') != os.environ['NC_E2E_MARK']:
    refuse('the ownership marker is missing or different')
companies = env['res.company'].search([])
if len(companies) != 1 or companies.name != os.environ['NC_E2E_COMPANY']:
    refuse('expected exactly one company named %r, found %s' % (os.environ['NC_E2E_COMPANY'], companies.mapped('name')))
company = companies
if company.country_id.code != 'AE' or company.currency_id.name != 'AED' or company.chart_template != 'ae':
    refuse('the company is not on ae/AED: %s %s %s' % (company.country_id.code, company.currency_id.name,
                                                       company.chart_template))
if env['ncollection.einvoice.provider'].with_context(active_test=False).search_count([]):
    refuse('an ASP provider exists')
if env['ncollection.einvoice.secret'].search_count([]):
    refuse('a provider credential exists')
env = env(context=dict(env.context, allowed_company_ids=company.ids))
ae = env.ref('base.ae')


def state(code):
    st = env['res.country.state'].search([('country_id', '=', ae.id), ('code', '=', code)])
    if len(st) != 1:
        refuse('emirate %s not found exactly once' % code)
    return st


def same(rec, vals):
    """The fields of ``vals`` that ``rec`` holds differently."""
    diff = []
    for key, want in vals.items():
        have = rec[key]
        if hasattr(have, '_name'):
            have = have.id
        if (have or False) != (want or False):
            diff.append('%s=%r (fixture %r)' % (key, have, want))
    return diff


def owned(name, model, find_domain, vals):
    """The fixture record ``name``: created once, then only compared; never adopted, never overwritten."""
    imd = env['ir.model.data'].search([('module', '=', MOD), ('name', '=', name)])
    if imd:
        rec = env[model].browse(imd.res_id).exists()
        if imd.model != model or not rec:
            refuse('%s.%s points at %s,%s which is not a live %s' % (MOD, name, imd.model, imd.res_id, model))
        diff = same(rec, vals)
        if diff:
            refuse('%s.%s differs from the fixture: %s' % (MOD, name, '; '.join(diff)))
        print('INFO unchanged %s %s,%s' % (name, model, rec.id))
        return rec
    clash = env[model].with_context(active_test=False).search(find_domain)
    if clash:
        refuse('%s %s already exists without the fixture id %s.%s' % (model, clash.ids, MOD, name))
    rec = env[model].create(vals)
    env['ir.model.data'].create({'module': MOD, 'name': name, 'model': model, 'res_id': rec.id, 'noupdate': True})
    print('INFO created %s %s,%s' % (name, model, rec.id))
    return rec


# ---- seller: the company partner (exists; empty fields are filled, a different value is a conflict)
seller = {
    'street': 'Al Maktoum Road', 'city': 'Dubai', 'state_id': state('DU').id, 'country_id': ae.id,
    'nc_tin': '1987654321', 'nc_trn': '198765432102003', 'company_registry': '112345678900003',
    'nc_legal_reg_type': 'TL', 'nc_legal_reg_authority': 'Dubai Department of Economy',
}
partner = company.partner_id


def unset(value):
    """Nothing stored: an empty many2one (False), a NULL (False / None) or an empty text ('')."""
    return value is None or value is False or value == ''


conflicts, missing = [], {}
for key, want in seller.items():
    have = partner[key]
    if hasattr(have, '_name'):
        have = have.id
    if have == want:
        continue
    if unset(have):
        missing[key] = want
    else:
        conflicts.append('%s=%r (fixture %r)' % (key, have, want))
if conflicts:
    refuse('the company partner already holds other values: %s' % '; '.join(conflicts))
if missing:
    partner.write(missing)
    print('INFO seller filled %s' % sorted(missing))
else:
    print('INFO seller unchanged')

# ---- buyer
buyer_vals = {
    'name': 'E2E UAE Buyer LLC (fictional)', 'company_type': 'company', 'street': 'Hamdan Street',
    'city': 'Abu Dhabi', 'state_id': state('AZ').id, 'country_id': ae.id,
    'nc_tin': '1345678901', 'nc_trn': '134567890123003', 'company_registry': '112345679000001',
    'nc_legal_reg_type': 'TL', 'nc_legal_reg_authority': 'DED',
}
buyer = owned('buyer', 'res.partner', [('name', '=', buyer_vals['name'])], buyer_vals)
if buyer.customer_rank < 1:
    buyer.customer_rank = 1  # show it in the customer pickers; carries no e-invoicing meaning

# ---- product (Goods, HS code; the default unit Units maps to UN/ECE C62)
product_vals = {
    'name': 'E2E UAE Widget (fictional)', 'default_code': 'E2E-UAE-G1', 'list_price': 100.0,
    'nc_item_type': 'G', 'nc_hs_code': '88098432324', 'uom_id': env.ref('uom.product_uom_unit').id,
}
owned('product', 'product.product', [('default_code', '=', 'E2E-UAE-G1')], product_vals)


# ---- tax map on the AE chart's own sale taxes
def sale_tax(name):
    tax = env['account.tax'].search([('company_id', '=', company.id), ('type_tax_use', '=', 'sale'),
                                     ('name', '=', name)])
    if len(tax) != 1:
        refuse('sale tax %r not found exactly once on the AE chart' % name)
    return tax


for xmlid, tax_name, extra in (('tax_map_s', '5% DU', {'pint_category': 'S'}),
                               ('tax_map_s_az', '5% AZ', {'pint_category': 'S'}),
                               ('tax_map_e', '0% EXT', {'pint_category': 'E', 'exemption_code': 'DL8.46.1'}),
                               ('tax_map_z', '0%', {'pint_category': 'Z'})):
    tax = sale_tax(tax_name)
    owned(xmlid, 'ncollection.einvoice.tax.map', [('company_id', '=', company.id), ('tax_id', '=', tax.id)],
          dict(company_id=company.id, tax_id=tax.id, **extra))

# ---- engine config (no ASP: transport needs a provider, and none exists)
owned('config', 'ncollection.einvoice.config', [('company_id', '=', company.id)],
      {'company_id': company.id, 'enabled': True, 'default_payment_means_code': '10'})

if mode == 'apply':
    env.cr.commit()
    print('STEP_OK seeded')
else:
    env.cr.rollback()
    print('STEP_OK check (rolled back)')
PY
); then
  echo "❌ seed step failed:" >&2; echo "$out" | tail -40 >&2; exit 1
fi
grep -q '^STEP_OK' <<<"$out" || { echo "❌ seed step did not complete:" >&2; echo "$out" | tail -40 >&2; exit 1; }
grep -E '^(STEP_OK|INFO)' <<<"$out" | sed 's/^/  [seed] /'
echo "✅ e2euae functional fixture: $MODE done (fictional seller, buyer, product, tax map, config; no invoice)"
