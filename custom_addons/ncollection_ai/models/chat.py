# -*- coding: utf-8 -*-
"""The chat widget's server half (P5-T06 / #63).

Two things live here and NOTHING else: per-user message history, and the
curated-prompt path that makes the widget usable while #375's free-text flag
is off.

WHY THE CURATED PATH IS NOT A #375 BYPASS. The decision recorded on #375 is
that no ARBITRARY USER PROSE reaches the LLM provider until someone
deliberately flips ``ncollection_ai.enable_free_text_questions``. The prompts
sent by ``send_suggested`` are the opposite of arbitrary user prose: the
client transmits a KEY, the text is resolved server-side from
``data/suggested_prompts.json``, and an unknown key is refused outright. What
can be sent is exactly what a reviewer can read in that file — the same
control shape as the #62 model whitelist. Typed input goes through
``send_typed``, which is a thin wrapper over ``ask()`` and therefore inherits
the flag, the secret scans and everything else; with the flag off it is
refused, and this module adds no way around that.

WHY HISTORY IS ITS OWN SMALL MODEL rather than mail.thread: a chat with the
assistant is a PRIVATE per-user log. Discuss's machinery (followers,
notifications, activities) exists to make conversations shared, which is the
exact property this must not have. A two-field model with a record rule is
the smaller and safer tool.
"""
import json
import logging
from datetime import timedelta

from odoo import api, fields, models, tools
from odoo.exceptions import UserError, ValidationError

# One definition of each policy constant (#374's lesson): the gate tuple and
# the question cap are ai_question.py's, imported rather than copied. The copy
# that drifts is an authorization check.
from ..concurrency import AiAdmission, global_limit
from .ai_question import _ALLOWED_GROUPS, _MAX_QUESTION_CHARS

# How much history the widget receives on open. A bound, not a paginator:
# the widget is an assistant, not an archive, and an unbounded read of a
# long-lived row set is how dashboards go slow one quarter after shipping.
_HISTORY_LIMIT = 40

# Server-side size policy for a stored row. A user row can never exceed the
# question cap ask() already enforces; an assistant row is bounded well above
# what max_answer_tokens produces (~4 chars/token) and well below anything that
# could make a transcript read expensive. The service TRUNCATES to these before
# writing; the model constraint is the backstop that makes "no oversized row"
# a property of the table rather than of one caller.
_MAX_ASSISTANT_CHARS = 20000

# Transcript retention: rows older than this many days are deleted by the daily
# job. Configurable per tenant (the parameter is seeded noupdate); <= 0 keeps
# rows forever. A transcript is a convenience, not a record, so the default is
# a quarter, not "forever".
RETENTION_PARAM = 'ncollection_ai.transcript_retention_days'
_DEFAULT_RETENTION_DAYS = 90

# The registry is static per process — a data file inside the module — so it
# is read once and cached at module level. dict.update keeps the reference
# stable without a `global` statement.
_CATALOG_CACHE = {}


def _prompt_catalog():
    """Load and cache data/suggested_prompts.json.

    ``tools.file_open`` rather than a hand-built path: it resolves through the
    addons path the way every other packaged data read in Odoo does, and it
    refuses paths outside the addons tree.
    """
    if not _CATALOG_CACHE:
        with tools.file_open(
                'ncollection_ai/data/suggested_prompts.json', 'r') as handle:
            _CATALOG_CACHE.update(json.load(handle))
    return _CATALOG_CACHE


_logger = logging.getLogger(__name__)

# Concurrency cap: see ncollection_ai/concurrency.py (an AI request holds a web worker for the provider's latency).
MAX_CONCURRENT_PARAM = 'ncollection_ai.max_concurrent_requests'
_DEFAULT_MAX_CONCURRENT = 2
# Server-wide cap (all tenants together), derived from Odoo's `workers` unless overridden; and the bounded wait.
MAX_GLOBAL_PARAM = 'ncollection_ai.max_concurrent_global'
SLOT_WAIT_PARAM = 'ncollection_ai.slot_wait_ms'
_DEFAULT_SLOT_WAIT_MS = 300
_MAX_SLOT_WAIT_MS = 2000


class AiChatMessage(models.Model):
    _name = 'ncollection.ai.chat.message'
    _description = 'AI assistant chat history (private per user)'
    _order = 'id'

    # ondelete='cascade': a deleted user's chat log is meaningless and keeping
    # it would be a retention liability, not a feature.
    user_id = fields.Many2one(
        'res.users', required=True, index=True, ondelete='cascade',
        default=lambda self: self.env.user)
    role = fields.Selection(
        [('user', 'User'), ('assistant', 'Assistant')], required=True)
    # Text, never Html: the assistant's output is UNTRUSTED DATA end to end.
    # It is stored as plain text here and escaped again client-side before the
    # widget's markdown subset is applied. An Html field would sanitise on
    # write and tempt someone to render it raw.
    body = fields.Text(required=True)

    @api.constrains('body', 'role')
    def _check_body_size(self):
        for record in self:
            cap = (_MAX_ASSISTANT_CHARS if record.role == 'assistant'
                   else _MAX_QUESTION_CHARS)
            if len(record.body or '') > cap:
                raise ValidationError(self.env._(
                    "A chat message cannot be longer than %s characters.", cap))

    @api.model
    def _retention_days(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            RETENTION_PARAM, default=str(_DEFAULT_RETENTION_DAYS))
        try:
            return int(raw)
        except (TypeError, ValueError):
            # A mistyped value must not silently turn deletion off (or on for
            # everything): fall back to the shipped default.
            return _DEFAULT_RETENTION_DAYS

    @api.model
    def _gc_transcripts(self):
        """Daily job: delete transcript rows older than the retention window.

        Returns how many rows were removed. sudo() on purpose: the job is the
        platform's housekeeping, no user has (or should have) unlink rights.
        """
        days = self._retention_days()
        if days <= 0:
            return 0
        cutoff = fields.Datetime.now() - timedelta(days=days)
        rows = self.sudo().search([('create_date', '<', cutoff)])
        count = len(rows)
        rows.unlink()
        return count


class AiChat(models.AbstractModel):
    _name = 'ncollection.ai.chat'
    # _require_any_group — the same shared gate the dashboards and ask() use.
    _inherit = 'ncollection.financial.gate.mixin'
    _description = 'AI chat widget service (P5-T06)'

    # ------------------------------------------------------------------ state
    @api.model
    def get_state(self, module_ctx=None, include_history=True):
        """Everything the widget needs to render, and nothing more.

        Returns ``{'enabled': False}`` for users outside the financial gate
        rather than raising: the widget's caller is EVERY backend session, and
        an exception here would put a traceback in every non-finance user's
        console for the crime of loading the web client. Not raising is a UX
        decision only — the send methods below still hard-refuse, because
        rendering nothing is not an authorization control (Standing Rule 4).

        ``include_history=False`` serves the widget's navigation-driven chip
        refresh, which wants ONLY the suggestions: re-reading 40 transcript
        rows on every route change to throw them away is the small version of
        the dashboard-goes-slow pattern _HISTORY_LIMIT's comment warns about.
        """
        if not any(self.env.user.has_group(g) for g in _ALLOWED_GROUPS):
            return {'enabled': False}

        catalog = _prompt_catalog()
        if not isinstance(module_ctx, str):
            module_ctx = 'default'
        keys = catalog['modules'].get(module_ctx) or catalog['modules']['default']
        # Labels only. The canonical question text stays server-side until it
        # is actually sent — the client never needs it to render a chip, and
        # minimum state is the contract.
        labels = self._chip_labels()
        suggestions = [
            {'key': key,
             'label': labels.get(key) or catalog['prompts'][key]['label']}
            for key in keys if key in catalog['prompts']
        ]

        # Explicit user_id domain even though the record rule enforces the
        # same thing: record rules do not apply to the superuser, and admin
        # (uid 1 in tests, base.group_system in life) reading everyone's chat
        # through the widget would be a leak with a helpful UI.
        history = []
        if include_history:
            history = self.env['ncollection.ai.chat.message'].search_read(
                [('user_id', '=', self.env.uid)],
                ['role', 'body', 'create_date'],
                order='id desc', limit=_HISTORY_LIMIT)
            history.reverse()  # oldest first, the way a transcript reads

        return {
            'enabled': True,
            'free_text': self.env['ncollection.ai.question']._free_text_enabled(),
            'suggestions': suggestions,
            'history': history,
        }

    def _chip_labels(self):
        """The suggestion chips' labels, in the viewer's language.

        The labels live in data/suggested_prompts.json, which Odoo's translation
        export cannot read, so an Arabic user saw English chips. The literals
        are therefore repeated here as ``self.env._()`` calls (extractable, and
        resolved in the caller's language at request time). The JSON label stays
        the fallback for a key added to the file but not yet listed here, and
        ``test_every_chip_label_is_translatable`` turns that drift into a red
        build. Only the CHIP LABEL is translated: the canonical question text
        that is sent to the provider (and echoed in the transcript) is the
        registry's English text, unchanged -- what crosses the boundary must be
        exactly what a reviewer can read in the JSON file (#375).
        """
        return {
            'workspace_summary': self.env._("Summarise our financial position"),
            'top_debtor': self.env._("Who owes us the most?"),
            'receivable_total': self.env._("Total outstanding receivables"),
            'unpaid_customers': self.env._("Customers with unpaid invoices"),
            'top_customers': self.env._("Top three customers"),
            'last_month_invoiced': self.env._("Last month's invoicing"),
            'invoicing_trend': self.env._("Invoicing trend"),
            'reporting_currency': self.env._("Reporting currency"),
            'best_month': self.env._("Best invoicing month"),
            'top_vs_receivables': self.env._("Top customer vs receivables"),
        }

    # ------------------------------------------------------------------ sends
    def _max_concurrent(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(
            MAX_CONCURRENT_PARAM, default=str(_DEFAULT_MAX_CONCURRENT))
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            return _DEFAULT_MAX_CONCURRENT

    def _server_limit(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(MAX_GLOBAL_PARAM)
        try:
            configured = int(raw) if raw else None
        except (TypeError, ValueError):
            configured = None
        return global_limit(tools.config['workers'], configured)

    def _slot_wait_seconds(self):
        raw = self.env['ir.config_parameter'].sudo().get_param(SLOT_WAIT_PARAM, default=str(_DEFAULT_SLOT_WAIT_MS))
        try:
            ms = int(raw)
        except (TypeError, ValueError):
            ms = _DEFAULT_SLOT_WAIT_MS
        return min(max(ms, 0), _MAX_SLOT_WAIT_MS) / 1000.0

    def _take_slot(self):
        """Reserve a server-wide AND a per-database AI slot (bounded wait) or refuse with a friendly message."""
        slot = AiAdmission(self.env.registry, self._max_concurrent(), self._server_limit(), self._slot_wait_seconds())
        try:
            granted = slot.acquire()
        except Exception:  # the maintenance database is unreachable: fail closed, never run unbounded
            _logger.warning("AI admission could not take a slot; refusing the request", exc_info=True)
            granted = False
        if not granted:
            raise UserError(self.env._(
                "The assistant is busy answering other questions. "
                "Please try again in a moment."))
        return slot

    def _resolve_suggested(self, key):
        """Gate + registry lookup. Returns (text shown in the transcript,
        canonical question sent to the provider).

        The key is the only client input, and it is treated as untrusted: it
        selects from the registry or it is refused. No client-supplied text
        participates in the prompt on this path, which is what lets it run
        with the #375 flag off. The provider always gets the registry's
        English text (what a reviewer can read in the JSON file); the
        TRANSCRIPT shows the chip label in the viewer's language, so an
        Arabic user does not see an English question they never typed.
        """
        self._require_any_group(_ALLOWED_GROUPS, self.env._(
            "The AI assistant is available to the Accountant, the CEO and "
            "the workspace owner."))

        prompts = _prompt_catalog()['prompts']
        entry = prompts.get(key) if isinstance(key, str) else None
        if not entry:
            # UserError, not a silent fallback: an unknown key is either a
            # stale client after an upgrade or someone probing the surface,
            # and both should see a refusal rather than a guess.
            raise UserError(self.env._(
                "Unknown suggested prompt. Reload the page and pick one of "
                "the offered suggestions."))
        display = self._chip_labels().get(key) or entry['label']
        return display, entry['question']

    @api.model
    def send_suggested(self, key):
        """Run one SERVER-OWNED prompt through the pipeline (batch delivery)."""
        display, question = self._resolve_suggested(key)
        slot = self._take_slot()
        try:
            result = self.env['ncollection.ai.question']._run_pipeline(question)
        finally:
            slot.release()
        return self._persist_exchange(display, result)

    def _stream_plan(self, kind, value):
        """Admission + preparation for the STREAMING controller.

        Private (not RPC-callable): the controller is the only caller, and it
        runs this inside the request, as the user, before a single byte is
        streamed - so every refusal (gate, #375 flag, secret scan, unknown
        key, budget) is decided with the same code and the same ordering as
        the batch methods. Returns the transcript text and the prepared
        (sanitised) prompt; nothing has been sent anywhere yet.
        """
        question_service = self.env['ncollection.ai.question']
        if kind == 'suggested':
            display, question = self._resolve_suggested(value)
        elif kind == 'typed':
            question = question_service._admit_typed(value)
            display = question
        else:
            raise UserError(self.env._("Unknown request."))
        return {'display': display,
                'prepared': question_service._prepare_pipeline(question)}

    @api.model
    def send_typed(self, question):
        """Free-typed input. A thin wrapper over ask(), ON PURPOSE.

        Every protection ask() has — the financial gate, the #375 free-text
        flag, the truncation cap, both secret scans — applies here because
        ask() itself runs, not a copy of it. With the flag off this raises
        before anything is read or sent, and no history is written.
        """
        slot = self._take_slot()
        try:
            result = self.env['ncollection.ai.question'].ask(question)
        finally:
            slot.release()
        # Mirror ask()'s own cap so the transcript shows what was processed,
        # not a 10k-character paste the pipeline never saw.
        return self._persist_exchange(
            (question or '')[:_MAX_QUESTION_CHARS], result)

    # ------------------------------------------------------------- internals
    def _persist_exchange(self, question, result):
        """Write both sides of one exchange, AFTER the pipeline succeeded.

        Called only once a real answer exists. Every refusal path — gate,
        flag, secret scan, unknown key, gateway failure — raises before this
        method, so a failed request leaves no transcript at all: a chat log
        showing an answer that was never given is worse than no log.
        """
        # The ACL grants users READ only. The service is the one writer, with
        # sudo() and an EXPLICIT user_id taken from the authenticated request
        # (never from client input), so a user cannot plant "assistant" rows
        # through normal ORM/RPC access - the transcript is the assistant's
        # own record again. Sizes are capped here and by the model constraint.
        Message = self.env['ncollection.ai.chat.message'].sudo()
        records = Message.create([
            {'user_id': self.env.uid, 'role': 'user',
             'body': (question or '')[:_MAX_QUESTION_CHARS]},
            {'user_id': self.env.uid, 'role': 'assistant',
             'body': (result.get('answer') or '')[:_MAX_ASSISTANT_CHARS]},
        ])
        return {
            'messages': [
                {'id': message.id, 'role': message.role, 'body': message.body,
                 'create_date': fields.Datetime.to_string(message.create_date)}
                for message in records
            ],
            'dropped_sections': result.get('dropped_sections', []),
            'usage': result.get('usage', {}),
        }
