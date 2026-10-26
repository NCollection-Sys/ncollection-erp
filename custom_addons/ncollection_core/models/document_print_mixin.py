# -*- coding: utf-8 -*-
"""View and Print of a business document: ONE report, ONE context, ONE rendering.

Odoo's own "Preview" of an invoice or a quotation opens the customer-portal page, a different template from the PDF
that Print produces, and some documents (a purchase order, a payment) have no preview at all. A model that inherits
this mixin declares, once, the report its Print uses (:meth:`_nc_print_report`) and the context it renders with
(:meth:`_nc_print_context`); then

* :meth:`action_nc_view` opens ``/ncollection/document/view/<model>/<id>`` in a new tab: the controller renders the
  document with that report and that context and answers with the PDF INLINE (the browser's own viewer, no download),
  named after the document, and
* :meth:`action_nc_print` is the report action Print runs with that same report and context,

so View and Print cannot render two different documents. Nothing here renders anything itself: the report engine
does, as the user, with the user's own access rights.
"""
from odoo import models


class NcDocumentPrintMixin(models.AbstractModel):
    _name = 'nc.document.print.mixin'
    _description = 'View and Print of a business document from one report'

    # The View controller serves only models that carry this marker (i.e. that opted in by inheriting this mixin).
    _nc_printable = True

    def _nc_print_report(self):
        """The ``ir.actions.report`` this document's Print uses. Every model that opts in defines it."""
        raise NotImplementedError

    def _nc_print_context(self):
        """The context Print renders with (and so View too). Nothing by default."""
        self.ensure_one()
        return {}

    def _nc_view_url(self):
        """Where View opens: the controller that serves this document inline (see controllers/document_view.py)."""
        self.ensure_one()
        return '/ncollection/document/view/%s/%d' % (self._name, self.id)

    def action_nc_view(self):
        """The printed document, opened in a new tab (not a second, different rendering of it)."""
        self.ensure_one()
        self.check_access('read')
        return {'type': 'ir.actions.act_url', 'target': 'new', 'url': self._nc_view_url()}

    def action_nc_print(self):
        """Print: the same report and context as :meth:`action_nc_view`. The action carries ``nc_print_in_tab``, and
        the web client opens it in a new tab as the PDF (static/src/js/print_in_tab.js) instead of downloading it:
        the browser's own viewer prints it."""
        self.ensure_one()
        return self._nc_print_report().with_context(**self._nc_print_context(), nc_print_in_tab=True).report_action(
            self.id, config=False)

    def action_nc_edit(self):
        """The document's own form, for a user who may change it (anyone else gets Odoo's own access error)."""
        self.ensure_one()
        self.check_access('write')
        return {
            'type': 'ir.actions.act_window', 'res_model': self._name, 'res_id': self.id,
            'view_mode': 'form', 'views': [(False, 'form')], 'target': 'current',
        }
