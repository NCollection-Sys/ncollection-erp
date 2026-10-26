# -*- coding: utf-8 -*-
from . import controllers
from . import models
from . import wizards

from .hooks import post_init_hook  # noqa: F401 (referenced by __manifest__.py)


def post_load():
    """Server-wide: put the release compatibility gate in front of every database's request handling, before any
    registry is loaded (release_gate.py). config/odoo.conf and odoo.prod.conf list this module in
    ``server_wide_modules`` so the gate exists from the first request on."""
    from . import release_gate
    release_gate.install()
