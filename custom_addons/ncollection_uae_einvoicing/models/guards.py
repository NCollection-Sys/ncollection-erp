# -*- coding: utf-8 -*-
"""Guards shared by the models whose create must not be steered by the caller."""


def without_unsafe_defaults(records, allowed):
    """``records`` in an environment whose context has no ``default_<field>`` key except for the fields in ``allowed``.

    Odoo fills every field the caller did NOT supply from ``default_<field>`` context keys, and a client can send any
    context. A whitelist that inspects only the supplied values would therefore be bypassed by
    ``context={'default_local_state': 'validated'}``. A user-facing create must run in this cleaned environment.

    The context is REPLACED (``env(context=...)``), not merged: ``with_context(**cleaned)`` would merge the cleaned
    keys over the original ones and leave every unsafe default in place."""
    cleaned = {
        key: value
        for key, value in records.env.context.items()
        if not (key.startswith('default_') and key.removeprefix('default_') not in allowed)
    }
    return records.with_env(records.env(context=cleaned))
