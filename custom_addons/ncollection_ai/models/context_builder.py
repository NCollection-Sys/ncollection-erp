# -*- coding: utf-8 -*-
"""Tenant-scoped context assembly for prompts (P5-T03 / #60).

ISOLATION IS STRUCTURAL, NOT PROCEDURAL
---------------------------------------
AI_PLATFORM_DESIGN.md §5 is explicit about what this module's acceptance
criterion actually means:

    "The context builder runs inside one tenant database and has no route to
    another. P5-T03's acceptance ('injection tests prove no cross-tenant data
    can enter a prompt') is then a test that the STRUCTURE holds, not a test
    fighting a shared component."

So there is no tenant filtering here, and there must never be. Every read goes
through ``self.env``, which IS one tenant's database — a registry bound to one
`db_name` at cursor level. There is no code path to another tenant to filter
*out*. Adding a `tenant_id` parameter would be a red flag, not a safeguard: it
would imply a shared store that needs partitioning, which is precisely the
architecture this platform does not have.

WHY IT GOES THROUGH THE AGGREGATION ENGINE
------------------------------------------
`ncollection.aggregation.engine` (P4-T01 / #54) already enforces model
availability and per-user readability, and it never raises — a spec that a
caller may not read returns None rather than an error. Reusing it means prompt
context inherits those checks instead of re-implementing them slightly
differently, which is how two code paths end up disagreeing about who may read
what.

CONTEXT-WINDOW TRUNCATION
-------------------------
A context that overflows the window does not fail politely: the provider either
errors or silently drops the *end*, which is where the most specific data
usually sits. So sections are dropped whole, lowest priority first, and the
result records what was dropped — a prompt that quietly lost half its evidence
should be visible, not a mystery.
"""
import json

from odoo import api, models

# ~4 characters per token. Rough, and honest about it: exact counting needs the
# provider's tokeniser, which lives in the satellite and would drag a dependency
# into the tenant database to save very little.
_CHARS_PER_TOKEN = 4

# Sections are dropped from the END of this list first. Ordering is the whole
# truncation policy: identity and totals are what almost every question needs,
# while long tails of individual rows are the first thing that can go.
_SECTION_PRIORITY = (
    'workspace',       # who this is — currency, company name
    'totals',          # headline figures
    'receivables',     # money owed, the most-asked subject
    'recent_invoices',
    'top_customers',
    'recent_orders',
)


class AiContextBuilder(models.AbstractModel):
    """Builds a context payload from THIS tenant's data. Nothing else exists."""

    _name = 'ncollection.ai.context'
    _description = 'AI prompt context builder (tenant-scoped)'

    # NOTE ON `order`: it must name the AGGREGATE ALIAS ('amount_total:sum
    # desc'), not the bare field ('amount_total desc'). Odoo's _read_group
    # rejects the bare form, and ncollection.aggregation.engine never raises —
    # it returns None for any failed spec — so a malformed order does not error,
    # it silently removes the whole section. That shipped a context missing
    # receivables and top_customers, which quietly made 6 of the 20 review
    # questions unanswerable with nothing in any log to say why.
    # test_default_specs_all_produce_rows() exists to catch exactly this.
    #
    # The default snapshot. Deliberately small: §4's token budgets are per
    # tenant per window, and a context that ships every row burns the budget on
    # data no question asked about.
    def _default_specs(self):
        return [
            {'key': 'receivables', 'model': 'account.move',
             'domain': [('move_type', '=', 'out_invoice'),
                        ('payment_state', '!=', 'paid'),
                        ('state', '=', 'posted')],
             'groupby': ['partner_id'],
             'aggregates': ['amount_residual:sum'],
             'limit': 20, 'order': 'amount_residual:sum desc'},
            {'key': 'recent_invoices', 'model': 'account.move',
             'domain': [('move_type', '=', 'out_invoice'),
                        ('state', '=', 'posted')],
             'groupby': ['invoice_date:month'],
             'aggregates': ['amount_total:sum'],
             # `order` is NOT optional here. _read_group defaults to ordering by
             # the groupby term ASCENDING, so with a limit this returned the
             # OLDEST 12 months — and unlike a rejected spec it fills
             # successfully with wrong data: nothing dropped, nothing logged,
             # and three review questions answered from stale figures. Only
             # visible once a tenant has more than 12 months of history, which
             # albarari does not, which is why local runs looked fine.
             'limit': 12, 'order': 'invoice_date:month desc'},
            {'key': 'top_customers', 'model': 'sale.order',
             'domain': [('state', 'in', ('sale', 'done'))],
             'groupby': ['partner_id'],
             'aggregates': ['amount_total:sum'],
             'limit': 10, 'order': 'amount_total:sum desc'},
        ]

    @api.model
    def build(self, specs=None, max_tokens=2000):
        """Assemble context for THIS tenant.

        Returns ``{'sections': {...}, 'dropped': [...], 'estimated_tokens': n}``.

        Note the signature: no tenant argument, and there must never be one.
        See the module docstring — `self.env` IS the tenant.
        """
        engine = self.env['ncollection.aggregation.engine']
        specs = self._default_specs() if specs is None else specs

        sections = {'workspace': self._workspace_facts()}
        for spec in specs:
            # aggregate() returns None for "not available to this caller" —
            # absent model, unlicensed, unreadable. A missing section is normal
            # (a tenant without `sale` has no top_customers) and must not be an
            # error, or every plan variation becomes a crash.
            result = engine.aggregate(spec)
            if result and result.get('rows'):
                sections[spec['key']] = self._label_rows(spec, result['rows'])

        return self._truncate(sections, max_tokens)

    def _label_rows(self, spec, rows):
        """Turn the engine's positional rows into named dicts.

        THIS IS A PII CONTROL, not cosmetics. The engine returns rows as nested
        lists — ``[[16, "Nakheel"], 101920.0]`` — with no field names anywhere.
        ncollection.ai.pii decides what to pseudonymise BY FIELD NAME, so an
        unlabelled row sails straight past it and real customer names reach the
        provider. That is exactly what happened: the review harness reported one
        pseudonym against a context containing six customer names.

        Labelling here rather than teaching the sanitiser to sniff ``[id, name]``
        pairs, because the sanitiser should keep deciding on names — a shape
        heuristic would be guessing, and it would still miss a bare string.

        It also improves context quality, which is the ticket's other criterion:
        ``{"partner_id": "Nakheel", "amount_residual:sum": 101920.0}`` is far
        easier for a model to reason over than a nested array.

        The many2one id is DROPPED. It is a tenant-internal identifier with no
        analytical value, and sending it would leak a stable cross-request
        handle that pseudonymisation is meant to remove.
        """
        labels = list(spec.get('groupby', [])) + list(spec.get('aggregates', []))
        labelled = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                labelled.append(row)      # already shaped; leave it alone
                continue
            item = {}
            for index, value in enumerate(row):
                name = labels[index] if index < len(labels) else 'value_%d' % index
                # many2one cells arrive as [id, display_name] — keep the name.
                if isinstance(value, (list, tuple)) and len(value) == 2 \
                        and isinstance(value[1], str):
                    value = value[1]
                item[name] = value
            labelled.append(item)
        return labelled

    def _workspace_facts(self):
        """Identity, so the model knows whose data it is reasoning about.

        Company NAME is included and is pseudonymised downstream by
        ncollection.ai.pii like any other identity — this module states facts,
        the sanitiser decides what may cross the boundary. Keeping those two
        jobs apart is what makes the PII policy auditable in one place.
        """
        company = self.env.company
        return {
            'company': company.name,
            'currency': company.currency_id.name,
            'today': str(self.env.cr.now().date()) if hasattr(self.env.cr, 'now')
            else None,
        }

    # --------------------------------------------------------------- truncation
    def _estimate_tokens(self, payload):
        return max(1, len(json.dumps(payload, default=str)) // _CHARS_PER_TOKEN)

    def _truncate(self, sections, max_tokens):
        """Drop whole sections, lowest priority first, until it fits.

        Whole sections rather than trailing characters: half a JSON array is not
        parseable context, and a model given a truncated structure will
        confabulate the missing part rather than notice it is missing.
        """
        dropped = []
        ordered = [key for key in _SECTION_PRIORITY if key in sections]
        # Anything not in the priority list is unranked, so it goes first —
        # a caller passing custom specs has not told us how to rank them.
        ordered = [k for k in sections if k not in _SECTION_PRIORITY] + ordered

        while self._estimate_tokens(sections) > max_tokens and len(ordered) > 1:
            victim = ordered.pop(0)
            del sections[victim]
            dropped.append(victim)

        return {
            'sections': sections,
            # Recorded, not silent: a prompt that lost half its evidence should
            # be visible in the response metadata.
            'dropped': dropped,
            'estimated_tokens': self._estimate_tokens(sections),
        }
