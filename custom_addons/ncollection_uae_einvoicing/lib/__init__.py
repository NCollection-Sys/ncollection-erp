# -*- coding: utf-8 -*-
"""Pure-Python core of the UAE e-invoicing engine: no ORM, no Odoo import, no network.

Everything compliance-relevant that can be decided from data alone lives here so it can be tested exhaustively and
cannot be influenced by the database. The Odoo models (models/) read accounting data, hand plain values in, and store
what comes back. Each module states its authority: ``OFFICIAL:<rule id>`` = a rule of the pinned PINT-AE Schematron,
``NCOLLECTION:<name>`` = an NCollection rule (never presented as official)."""
