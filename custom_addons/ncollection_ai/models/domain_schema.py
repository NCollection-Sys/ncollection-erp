# -*- coding: utf-8 -*-
"""Strict, model-scoped domain whitelist and validator (P5-T05 / #62).

**THIS FILE IS THE SECURITY BOUNDARY.** Provider output is DATA, never code: it
is parsed with ``json.loads`` and then checked leaf by leaf against the table
below. Nothing here evaluates, executes or imports anything derived from that
output — there is no ``eval``, no ``exec``, no ``safe_eval``, and no ORM call
anywhere in this module. It is deliberately pure stdlib: a file that cannot
reach the database cannot leak from it, and that is a property a reader can
check by looking at the imports rather than by trusting a comment.

FIELD-TABLE PROVENANCE. Every entry was read off the LIVE models in this
deployment — ``ir_model_fields`` and ``ir_model_fields_selection`` on the
``ncollection`` database, Odoo 19 — not inferred from how the repository
happens to use them. Selection values are the COMPLETE set the field accepts,
which matters in both directions: a missing value is a false refusal, an
invented one is an accepted-but-wrong filter.

Two consequences of reading the real models rather than guessing:

* ``account.move.user_id`` is ``store=False`` here, so it is ABSENT. A
  non-stored field cannot be searched, and allowlisting one would produce a
  domain that validates and then fails at execution — the worst kind of pass.
* ``sale.order.state`` is ``draft, sent, sale, cancel``. There is no ``done``
  in Odoo 19 (order locking moved to its own field), so a question about
  "completed orders" maps to ``sale``, not to a state that no longer exists.

The whitelist IS the boundary (#62 §6): a field absent from this table is not
merely unsupported, it is unreachable. Adding one is a deliberate act that
should come with the same live verification these did.
"""

import re

# ---------------------------------------------------------------------------
# Rejection
# ---------------------------------------------------------------------------


class DomainRejected(ValueError):
    """A provider payload failed validation. Carries no provider text.

    The message is written for the person who has to act on it, but it never
    echoes the model's output back: a refusal that quotes the thing it refused
    hands an attacker a way to get arbitrary text rendered downstream.
    """


# ---------------------------------------------------------------------------
# Structural limits
# ---------------------------------------------------------------------------

#: Maximum nesting of boolean operators. Three is enough for
#: "A and B and (C or D)" and far short of anything a person would type.
MAX_DEPTH = 3

#: Maximum number of leaves (conditions). A legitimate search question does not
#: produce twenty conditions; a runaway or adversarial generation does.
MAX_LEAVES = 20

#: Maximum tokens overall — leaves plus boolean operators. Bounds the work the
#: validator itself does, so a hostile payload cannot make validation expensive.
MAX_TOKENS = 45

#: Maximum characters in a single string value. Long values are how a payload
#: smuggles prose (or a prompt) through a field that looks like a filter.
MAX_VALUE_CHARS = 200

#: Maximum members of an ``in`` / ``not in`` list.
MAX_LIST_MEMBERS = 20

_BOOL_OPS = frozenset({'&', '|'})
_NOT_OP = '!'

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_DATETIME_RE = re.compile(r'^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2})?$')

# ---------------------------------------------------------------------------
# Operators, by field type
# ---------------------------------------------------------------------------
#
# Deliberately NOT included anywhere: `=like`, `=ilike`, `child_of`,
# `parent_of`, `any`, `not any`. The first two let a pattern be anchored in
# ways that make enumeration cheap; the rest TRAVERSE to other models, which
# would step straight around the four-model whitelist this file exists to
# enforce.
_OPS_TEXT = frozenset({'=', '!=', 'like', 'ilike', 'not like', 'not ilike',
                       'in', 'not in'})
_OPS_NUMBER = frozenset({'=', '!=', '<', '<=', '>', '>='})
_OPS_TEMPORAL = frozenset({'=', '!=', '<', '<=', '>', '>='})
_OPS_BOOLEAN = frozenset({'=', '!='})
_OPS_SELECTION = frozenset({'=', '!=', 'in', 'not in'})
_OPS_MANY2ONE = frozenset({'=', '!=', 'in', 'not in'})

_OPS_BY_TYPE = {
    'char': _OPS_TEXT,
    'text': _OPS_TEXT,
    'integer': _OPS_NUMBER,
    'float': _OPS_NUMBER,
    'monetary': _OPS_NUMBER,
    'date': _OPS_TEMPORAL,
    'datetime': _OPS_TEMPORAL,
    'boolean': _OPS_BOOLEAN,
    'selection': _OPS_SELECTION,
    'many2one': _OPS_MANY2ONE,
}

# ---------------------------------------------------------------------------
# The whitelist
# ---------------------------------------------------------------------------
#
# Shape: model -> field -> spec. `type` is the verified ttype; `values` is the
# complete selection set where the type is `selection`.
#
# many2one fields are matched BY ID ONLY. Allowing a name string would mean
# resolving it against the related model — a read of res.partner / crm.stage /
# account.journal — and this module performs no ORM access at all. The consumer
# resolves names if it wants them; the mapper does not.
ALLOWED = {
    'sale.order': {
        'name': {'type': 'char'},
        'state': {'type': 'selection',
                  'values': ('draft', 'sent', 'sale', 'cancel')},
        'date_order': {'type': 'datetime'},
        'create_date': {'type': 'datetime'},
        'amount_total': {'type': 'monetary'},
        'amount_untaxed': {'type': 'monetary'},
        'origin': {'type': 'char'},
        'partner_id': {'type': 'many2one'},
        'user_id': {'type': 'many2one'},
        'team_id': {'type': 'many2one'},
        'company_id': {'type': 'many2one'},
        'currency_id': {'type': 'many2one'},
    },
    'account.move': {
        'name': {'type': 'char'},
        'move_type': {'type': 'selection',
                      'values': ('entry', 'out_invoice', 'out_refund',
                                 'in_invoice', 'in_refund', 'out_receipt',
                                 'in_receipt')},
        'state': {'type': 'selection',
                  'values': ('draft', 'posted', 'cancel')},
        'payment_state': {'type': 'selection',
                          'values': ('not_paid', 'in_payment', 'paid',
                                     'partial', 'reversed', 'blocked',
                                     'invoicing_legacy')},
        'invoice_date': {'type': 'date'},
        'invoice_date_due': {'type': 'date'},
        'create_date': {'type': 'datetime'},
        'amount_total': {'type': 'monetary'},
        'amount_untaxed': {'type': 'monetary'},
        'amount_residual': {'type': 'monetary'},
        'partner_id': {'type': 'many2one'},
        'journal_id': {'type': 'many2one'},
        'company_id': {'type': 'many2one'},
        'currency_id': {'type': 'many2one'},
        # user_id is store=False on this model — see the module docstring.
    },
    'stock.picking': {
        'name': {'type': 'char'},
        'state': {'type': 'selection',
                  'values': ('draft', 'waiting', 'confirmed', 'assigned',
                             'done', 'cancel')},
        'move_type': {'type': 'selection', 'values': ('direct', 'one')},
        'priority': {'type': 'selection', 'values': ('0', '1')},
        'scheduled_date': {'type': 'datetime'},
        'date_done': {'type': 'datetime'},
        'date_deadline': {'type': 'datetime'},
        'create_date': {'type': 'datetime'},
        'origin': {'type': 'char'},
        'partner_id': {'type': 'many2one'},
        'picking_type_id': {'type': 'many2one'},
        'user_id': {'type': 'many2one'},
        'company_id': {'type': 'many2one'},
    },
    'crm.lead': {
        'name': {'type': 'char'},
        'type': {'type': 'selection', 'values': ('lead', 'opportunity')},
        'priority': {'type': 'selection', 'values': ('0', '1', '2', '3')},
        'active': {'type': 'boolean'},
        'expected_revenue': {'type': 'monetary'},
        'probability': {'type': 'float'},
        'date_deadline': {'type': 'date'},
        'create_date': {'type': 'datetime'},
        'stage_id': {'type': 'many2one'},
        'partner_id': {'type': 'many2one'},
        'user_id': {'type': 'many2one'},
        'team_id': {'type': 'many2one'},
        'company_id': {'type': 'many2one'},
    },
}

ALLOWED_MODELS = tuple(sorted(ALLOWED))


def allowed_models():
    """The four models this mapper may ever produce a domain for."""
    return ALLOWED_MODELS


def schema_for(model):
    """The field table for `model`, or raise if it is not whitelisted."""
    try:
        return ALLOWED[model]
    except KeyError:
        raise DomainRejected(
            "Model %r is not available to the search mapper." % model) from None


# ---------------------------------------------------------------------------
# Value checking
# ---------------------------------------------------------------------------


def _reject(message):
    raise DomainRejected(message)


def _check_scalar(field, spec, value):
    """One non-list value against the field's declared type."""
    ftype = spec['type']

    # bool BEFORE int, deliberately: in Python `True` IS an int, so a numeric
    # check accepts it and a domain of ('amount_total', '>', True) would mean
    # "> 1" without anyone having written a number.
    if isinstance(value, bool) and ftype != 'boolean':
        _reject("Field %r does not take a true/false value." % field)

    if ftype == 'boolean':
        if not isinstance(value, bool):
            _reject("Field %r takes only true or false." % field)
    elif ftype in ('integer',):
        if not isinstance(value, int):
            _reject("Field %r takes a whole number." % field)
    elif ftype in ('float', 'monetary'):
        if not isinstance(value, (int, float)):
            _reject("Field %r takes a number." % field)
    elif ftype == 'selection':
        if not isinstance(value, str) or value not in spec['values']:
            _reject("Field %r has no option %r." % (field, _clip(value)))
    elif ftype == 'many2one':
        # By id only — see the whitelist comment.
        if not isinstance(value, int):
            _reject("Field %r is matched by record id." % field)
    elif ftype == 'date':
        # fullmatch, NOT match: `$` also matches before a trailing newline, so
        # `.match()` would accept "2026-01-01\n" — a value that then travels on
        # to the consumer carrying whatever follows it (#377, caught by
        # scripts/ci/invariants.py R8 on the first run of this file).
        if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
            _reject("Field %r takes a date as YYYY-MM-DD." % field)
    elif ftype == 'datetime':
        if not isinstance(value, str) or not _DATETIME_RE.fullmatch(value):
            _reject("Field %r takes a date or date and time." % field)
    elif ftype in ('char', 'text'):
        if not isinstance(value, str):
            _reject("Field %r takes text." % field)
        if len(value) > MAX_VALUE_CHARS:
            _reject("The value for %r is too long." % field)
    else:  # pragma: no cover - a type reaching here means the table is wrong
        _reject("Field %r has an unsupported type." % field)


def _clip(value):
    """A short, safe rendering of a rejected value for the message."""
    text = value if isinstance(value, str) else repr(value)
    return text[:32]


def _check_value(field, spec, operator, value):
    if operator in ('in', 'not in'):
        if not isinstance(value, list):
            _reject("Operator %r on %r needs a list of values."
                    % (operator, field))
        if not value:
            _reject("Operator %r on %r needs at least one value."
                    % (operator, field))
        if len(value) > MAX_LIST_MEMBERS:
            _reject("Too many values listed for %r." % field)
        for member in value:
            _check_scalar(field, spec, member)
        return
    if isinstance(value, (list, dict, tuple)):
        _reject("Field %r does not take a list here." % field)
    _check_scalar(field, spec, value)


def _check_leaf(leaf, schema):
    if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
        _reject("A search condition must have exactly three parts.")
    field, operator, value = leaf

    if not isinstance(field, str):
        _reject("A field name must be text.")
    # Dotted traversal is how a domain leaves its model. Rejected outright
    # rather than resolved, because resolving it is exactly the step that would
    # put a non-whitelisted model back in reach.
    if '.' in field:
        _reject("Field %r reaches into another record and is not allowed."
                % _clip(field))
    if field not in schema:
        _reject("Field %r is not available for this search." % _clip(field))

    if not isinstance(operator, str):
        _reject("An operator must be text.")
    spec = schema[field]
    permitted = _OPS_BY_TYPE[spec['type']]
    if operator not in permitted:
        _reject("Operator %r cannot be used on %r." % (_clip(operator), field))

    _check_value(field, spec, operator, value)
    return [field, operator, value]


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------


def _walk(tokens, index, depth, state):
    """Consume exactly one expression starting at `index`; return the next index.

    Odoo domains are PREFIX notation in a flat list, so "well formed" is an
    arity property, not a bracket-matching one: `&` and `|` each consume two
    following expressions, `!` consumes one, and a leaf consumes itself. A list
    that merely *looks* like a domain — `['&', leaf]` — is rejected here rather
    than surviving to fail obscurely at execution.
    """
    if depth > MAX_DEPTH:
        _reject("This search is nested more deeply than allowed.")
    if index >= len(tokens):
        _reject("The search ends in the middle of a condition.")

    token = tokens[index]
    if isinstance(token, str) and token in _BOOL_OPS:
        after_first = _walk(tokens, index + 1, depth + 1, state)
        return _walk(tokens, after_first, depth + 1, state)
    if isinstance(token, str) and token == _NOT_OP:
        return _walk(tokens, index + 1, depth + 1, state)
    if isinstance(token, str):
        _reject("%r is not a valid search operator." % _clip(token))

    state['leaves'] += 1
    if state['leaves'] > MAX_LEAVES:
        _reject("This search has too many conditions.")
    state['out'].append(_check_leaf(token, state['schema']))
    return index + 1


def validate(model, raw_domain):
    """Validate `raw_domain` for `model`; return a clean, plain domain.

    Returns a NEW structure built from checked parts — never the caller's
    object — so nothing that arrived in the payload can survive by reference.
    Raises :class:`DomainRejected` on anything else.
    """
    schema = schema_for(model)

    # An empty domain is legitimate (`[]` matches everything) but it is not an
    # ANSWER to a question, and silently returning "everything" for a question
    # the model did not understand is the wrong failure. The mapper asks for a
    # refusal instead.
    if raw_domain == []:
        _reject("No search conditions were produced for this question.")

    if not isinstance(raw_domain, list):
        _reject("A search must be a list of conditions.")
    if len(raw_domain) > MAX_TOKENS:
        _reject("This search is too large.")

    # Normalise tuples to lists for the arity walk; anything else stays as-is
    # so the leaf checker can reject it with a precise message.
    tokens = [list(t) if isinstance(t, tuple) else t for t in raw_domain]

    state = {'leaves': 0, 'out': [], 'schema': schema}
    consumed = _walk(tokens, 0, 0, state)
    if consumed != len(tokens):
        _reject("This search has more parts than its conditions use.")

    # Rebuild in the original order: operators as-is, leaves from the checked
    # copies, so the returned value shares nothing with the input.
    checked = iter(state['out'])
    result = []
    for token in tokens:
        if isinstance(token, str):
            result.append(token)
        else:
            result.append(next(checked))
    return result
