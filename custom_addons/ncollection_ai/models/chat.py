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

from odoo import api, fields, models, tools
from odoo.exceptions import UserError

# One definition of each policy constant (#374's lesson): the gate tuple and
# the question cap are ai_question.py's, imported rather than copied. The copy
# that drifts is an authorization check.
from .ai_question import _ALLOWED_GROUPS, _MAX_QUESTION_CHARS

# How much history the widget receives on open. A bound, not a paginator:
# the widget is an assistant, not an archive, and an unbounded read of a
# long-lived row set is how dashboards go slow one quarter after shipping.
_HISTORY_LIMIT = 40

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
    @api.model
    def send_suggested(self, key):
        """Run one SERVER-OWNED prompt through the pipeline.

        The key is the only client input, and it is treated as untrusted: it
        selects from the registry or it is refused. No client-supplied text
        participates in the prompt on this path, which is what lets it run
        with the #375 flag off.
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

        question = entry['question']
        result = self.env['ncollection.ai.question']._run_pipeline(question)
        return self._persist_exchange(question, result)

    @api.model
    def send_typed(self, question):
        """Free-typed input. A thin wrapper over ask(), ON PURPOSE.

        Every protection ask() has — the financial gate, the #375 free-text
        flag, the truncation cap, both secret scans — applies here because
        ask() itself runs, not a copy of it. With the flag off this raises
        before anything is read or sent, and no history is written.
        """
        result = self.env['ncollection.ai.question'].ask(question)
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
        Message = self.env['ncollection.ai.chat.message']
        records = Message.create([
            {'role': 'user', 'body': question},
            {'role': 'assistant', 'body': result.get('answer') or ''},
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
