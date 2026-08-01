# -*- coding: utf-8 -*-
"""Tenant-side aggregation engine (P4-T01).

The single choke point for every dashboard query in Phases 4-5 and, later, the
AI context engine (P5-T03). ARCHITECTURE_DATA_PLATFORM §9: "the P4-T01 engine is
the single choke point for dashboard queries ... No widget queries models
directly."

Four rules shape this module:

1. **Dependencies stay soft.** ``ncollection_core`` depends on ``base``, ``web``
   and ``ncollection_branding`` only. The engine aggregates ``sale`` /
   ``account`` / ``stock`` / ``hr`` models through registry + read checks, never
   through ``depends``, so it cannot force an app into a tenant's module set and
   leaves P1-T09 menu visibility and P1-T10 enforcement untouched. This mirrors
   the P1-T17 dashboard (``models/dashboard/dashboard_data.py``).

2. **Licensing is inherited, never re-implemented.** A spec whose model raises
   ``AccessError`` on read is dropped, exactly as P1-T10 Ring 2 intends. The
   engine never consults the plan itself — duplicating license logic is how the
   two rings drift apart.

3. **Fail-open, never fail-blank.** Any uncertain path returns "no result for
   this spec" and logs; it never raises into a caller. A dashboard missing a
   tile is a UX bug, a dashboard that 500s is an outage. This is the governing
   principle of ``ncollection_core`` (see ``license_enforcement.py``).

4. **Aggregation only — never financial computation.** ADR #15 / FPA §7 assign
   financial figures to ``ncollection_account_reports`` / ``_analytics``
   (#111 / #120). This engine caches and fans out; it does not decide what a
   number means. Financial specs carry a HANDOFF marker like the P1-T17
   providers do.

The caching layer lands in the next commit; every read here is uncached so the
correctness of the aggregation path can be tested on its own.
"""

import logging

from odoo import api, models
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)

# Aggregate suffixes Odoo's ``_read_group`` understands. Specs are validated
# against this set so a typo surfaces as a dropped spec with a loud log line
# rather than an opaque ORM error deep inside a dashboard request.
VALID_AGGREGATES = (
    '__count',
    ':sum', ':avg', ':min', ':max', ':count', ':count_distinct',
    ':array_agg', ':bool_and', ':bool_or',
)


class NCollectionAggregationEngine(models.AbstractModel):
    """Read-only, cached aggregation service shared by every dashboard.

    Consumers pass declarative *specs* rather than calling ``_read_group``
    themselves; that indirection is what makes a cache (and later a read-only
    standby, ARCHITECTURE_DATA_PLATFORM line 208) a one-place change instead of
    a change in every widget.
    """

    _name = 'ncollection.aggregation.engine'
    _description = 'NCollection Aggregation Engine'

    # ------------------------------------------------------------------
    # Availability (soft dependencies)
    # ------------------------------------------------------------------

    @api.model
    def _model_available(self, model_name):
        """True when ``model_name`` exists in this database's registry."""
        return model_name in self.env

    @api.model
    def _model_readable(self, model_name):
        """True when the model is installed AND this user may actually read it.

        Two distinct negatives collapse into one answer because for an
        aggregation they mean the same thing — "no data for you":
          * not in the registry -> the plan never installed the app;
          * AccessError on read -> installed, but P1-T10 Ring 2 says the plan
            does not license it for this user.

        A cheap ``search([], limit=1)`` is what triggers Ring 2.
        """
        if not self._model_available(model_name):
            return False
        try:
            self.env[model_name].search([], limit=1)
            return True
        except (AccessError, UserError):
            return False

    # ------------------------------------------------------------------
    # Spec validation
    # ------------------------------------------------------------------

    @api.model
    def _validate_spec(self, spec):
        """Return an error string, or None when the spec is well-formed.

        Validating up front keeps a malformed spec from reaching ``_read_group``
        where the resulting error is far from its cause. Callers get the spec
        dropped and a log line naming the key.
        """
        if not isinstance(spec, dict):
            return 'spec is not a dict'
        key = spec.get('key')
        if not key or not isinstance(key, str):
            return 'spec has no string "key"'
        model_name = spec.get('model')
        if not model_name or not isinstance(model_name, str):
            return 'spec %r has no string "model"' % key
        aggregates = spec.get('aggregates') or []
        if not isinstance(aggregates, (list, tuple)):
            return 'spec %r "aggregates" is not a list' % key
        for aggregate in aggregates:
            if not isinstance(aggregate, str) or not aggregate.endswith(VALID_AGGREGATES):
                return 'spec %r has unsupported aggregate %r' % (key, aggregate)
        groupby = spec.get('groupby') or []
        if not isinstance(groupby, (list, tuple)):
            return 'spec %r "groupby" is not a list' % key
        if not aggregates and not groupby:
            return 'spec %r requests neither aggregates nor groupby' % key
        domain = spec.get('domain')
        if domain is not None and not isinstance(domain, (list, tuple)):
            return 'spec %r "domain" is not a list' % key
        return None

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @api.model
    def aggregate(self, spec):
        """Run one aggregation spec. Returns ``{'key', 'rows'}`` or None.

        None means "this spec produced nothing for this caller" — model absent,
        not licensed, malformed, or the read failed. Never raises.

        Spec keys:
          ``key``        str, identifies the result to the caller (required)
          ``model``      str, technical model name (required)
          ``domain``     list, Odoo domain (default ``[]``)
          ``groupby``    list of groupby specs (default ``[]``)
          ``aggregates`` list like ``['amount_total:sum']`` (default ``[]``)
          ``limit`` / ``offset`` / ``order`` passed through to ``_read_group``
        """
        error = self._validate_spec(spec)
        if error:
            _logger.warning('Aggregation spec rejected: %s', error)
            return None

        model_name = spec['model']
        if not self._model_readable(model_name):
            # Not an error: the expected outcome on a tenant whose plan omits
            # this app, or for a user Ring 2 denies.
            return None

        try:
            rows = self._read_group_uncached(
                model_name,
                list(spec.get('domain') or []),
                list(spec.get('groupby') or []),
                list(spec.get('aggregates') or []),
                limit=spec.get('limit'),
                offset=spec.get('offset'),
                order=spec.get('order'),
            )
        except (AccessError, UserError):
            # Ring 2 can also deny at read_group time (a rule the cheap probe in
            # _model_readable did not trip). Same meaning: no data for you.
            return None
        except Exception:  # pragma: no cover - defensive fail-open
            _logger.exception(
                'Aggregation %r on %s failed — spec dropped (fail-open).',
                spec.get('key'), model_name)
            return None

        return {'key': spec['key'], 'rows': rows}

    @api.model
    def aggregate_many(self, specs):
        """Run several specs. Returns ``{key: rows}``, omitting dropped specs.

        Omission (rather than a None value) is deliberate: a consumer iterating
        the result never has to special-case "present but empty" against
        "absent", which is the distinction that leaks license state into the UI.
        """
        results = {}
        for spec in specs or []:
            result = self.aggregate(spec)
            if result is not None:
                results[result['key']] = result['rows']
        return results

    @api.model
    def _read_group_uncached(self, model_name, domain, groupby, aggregates,
                             limit=None, offset=None, order=None):
        """The raw ORM read. Isolated so the cache layer wraps exactly one call.

        Returns a list of plain tuples — ``_read_group`` yields recordsets for
        many2one groupby values, which are neither JSON-serialisable nor safe to
        cache across environments. Flattening to ``(id, display_name)`` here
        keeps the cached payload inert data.
        """
        kwargs = {}
        if limit is not None:
            kwargs['limit'] = limit
        if offset is not None:
            kwargs['offset'] = offset
        if order is not None:
            kwargs['order'] = order
        rows = self.env[model_name]._read_group(
            domain, groupby, aggregates, **kwargs)
        return [tuple(self._flatten_cell(cell) for cell in row) for row in rows]

    @api.model
    def _flatten_cell(self, cell):
        """Reduce a ``_read_group`` cell to inert, serialisable data."""
        if isinstance(cell, models.BaseModel):
            # A grouped many2one: keep identity AND label so a consumer never
            # has to re-read the record (which would defeat the cache).
            if len(cell) == 1:
                return (cell.id, cell.display_name)
            return [(record.id, record.display_name) for record in cell]
        return cell
