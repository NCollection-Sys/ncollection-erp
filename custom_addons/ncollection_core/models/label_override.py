"""Editable interface text (labels) and platform terminology, on Odoo's OWN translation storage.

WHERE THE TEXT LIVES (Odoo 19, verified):
  * menu / window-action names, field labels and help (``ir.model.fields``), selection labels
    (``ir.model.fields.selection``) and view texts (``ir.ui.view.arch_db`` terms) are translated jsonb
    columns, one value
    per language, en_US included. The web client reads them in the user's language (field labels through
    ``ir.model.fields.get_field_string``, cached; views through the combined arch, cached).
  * Python ``_()`` / JS ``_t()`` strings are loaded from each module's own ``i18n`` files and cannot be
  changed from the
    database. They are NOT editable here (coverage matrix: docs/markdown/LABELS_AND_TERMINOLOGY.md).

SCOPE. Those columns are database-wide, and a database is one tenant: an override is tenant-wide by construction. It
is never per company (that would need a parallel string store read by every view, which Odoo has no hook for).

PRECEDENCE (lowest first): the module's original text  <  platform terminology rule  <  tenant label override.
Module upgrades reload the original text, so everything is re-applied at registry load whenever the installed module
versions or the rules / overrides changed (one signature in ir.config_parameter): idempotent, cheap when
nothing changed.

NEVER: technical names, xml ids, field names, model names, selection KEYS, view structure (only existing TEXT terms are
replaced, never an attribute or a node), access rights or rule texts. Values are plain text (no markup).
"""
import hashlib
import json
import logging
import re


from odoo import api, fields, models, tools
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)

SIGNATURE_PARAM = 'ncollection_core.label_signature'
MAX_LABEL = 120
MAX_HELP = 1000
FORBIDDEN_CHARS = re.compile(r'[<>{}\x00-\x08\x0b\x0c\x0e-\x1f]')
# Models whose texts carry security meaning: never relabelled by a tenant.
PROTECTED_MODELS = {'res.groups', 'ir.rule', 'ir.model.access', 'ir.config_parameter', 'res.users.apikeys',
                    'ncollection.role', 'auditlog.rule', 'auditlog.log'}
COMPANY_SCOPED = ('field', 'help', 'menu')
TARGETS = [('menu', 'Menu'), ('action', 'Page Title (Action)'), ('field', 'Field Label'), ('help', 'Field Help'),
           ('selection', 'Choice Label'), ('view', 'Screen Text (Button, Tab, Section)')]


def _clean(env, value, limit):
    value = (value or '').strip()
    if not value:
        raise ValidationError(env._("The text cannot be empty."))
    if len(value) > limit:
        raise ValidationError(env._("The text is longer than %(limit)s characters.", limit=limit))
    if FORBIDDEN_CHARS.search(value):
        raise ValidationError(env._("Use plain text: < > { } and control characters are not allowed."))
    return value


class LabelRule(models.Model):
    """A platform terminology rule: whole-word, case-preserving replacement in ONE language, e.g. Vendor -> Supplier.
    Platform data (Platform Administrator / base.group_system), read-only to the tenant."""
    _name = 'ncollection.label.rule'
    _description = 'Terminology Rule'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    lang = fields.Char(string='Language', required=True, default='en_US')
    pairs = fields.Text(string='Replacements', required=True,
                        help="One replacement per line: word => replacement (singular and plural on separate lines). "
                             "Whole words only; the capitalisation of each occurrence is preserved.")
    last_applied = fields.Datetime(readonly=True)
    last_count = fields.Integer(string='Texts Changed (last run)', readonly=True)

    def _pairs(self):
        self.ensure_one()
        out = []
        for line in (self.pairs or '').splitlines():
            if '=>' not in line:
                continue
            old, new = (part.strip() for part in line.split('=>', 1))
            if old and new:
                out.append((_clean(self.env, old, MAX_LABEL), _clean(self.env, new, MAX_LABEL)))
        return out

    @api.constrains('pairs')
    def _check_pairs(self):
        for rule in self:
            if not rule._pairs():
                raise ValidationError(self.env._("Write at least one replacement as: word => replacement."))

    def _replacer(self):
        pairs = [pair for rule in self for pair in rule._pairs()]
        if not pairs:
            return None
        table = {old.lower(): new for old, new in pairs}
        pattern = re.compile(r'\b(' + '|'.join(re.escape(old) for old, _new in
                                               sorted(pairs, key=lambda p: -len(p[0]))) + r')\b', re.IGNORECASE)

        def keep_case(match):
            word = match.group(0)
            new = table[word.lower()]
            if word.isupper():
                return new.upper()
            if word[0].isupper():
                return new[0].upper() + new[1:]
            return new[0].lower() + new[1:]

        return lambda text: pattern.sub(keep_case, text) if text else text


class LabelOverride(models.Model):
    """One tenant's own text for one interface element, in one language."""
    _name = 'ncollection.label.override'
    _description = 'Label Override'
    _order = 'target_type, id'
    _rec_name = 'value'

    active = fields.Boolean(default=True)
    target_type = fields.Selection(TARGETS, string='Element', required=True)
    lang = fields.Char(string='Language', required=True, default=lambda self: self.env.lang or 'en_US')
    menu_id = fields.Many2one('ir.ui.menu', ondelete='cascade')
    action_id = fields.Many2one('ir.actions.act_window', ondelete='cascade')
    field_id = fields.Many2one('ir.model.fields', ondelete='cascade')
    selection_id = fields.Many2one('ir.model.fields.selection', string='Choice', ondelete='cascade')
    view_id = fields.Many2one('ir.ui.view', string='Screen', ondelete='cascade')
    source_term = fields.Char(string='Text on the Screen',
                              help="The exact text as the screen shows it in this language.")
    original = fields.Char(string='Original Text', readonly=True, copy=False)
    value = fields.Char(string='New Text', required=True)
    company_id = fields.Many2one(
        'res.company', string='Only for Company', ondelete='cascade',
        help="Empty: the whole workspace (Odoo's own translation of the text is changed). Set: only users working in "
             "this company see the new text; it is layered on when labels and menus are read, nothing shared changes. "
             "Company scope covers field labels, field help and menu names.")
    company_scope_note = fields.Char(compute='_compute_scope_note', string='Applies To')

    _unique_target = models.Constraint(
        'UNIQUE NULLS NOT DISTINCT '
        '(target_type, lang, menu_id, action_id, field_id, selection_id, view_id, source_term, company_id)',
        'This text already has an override in this language.')

    def _compute_scope_note(self):
        for rec in self:
            rec.company_scope_note = (self.env._("Only %s", rec.company_id.name) if rec.company_id
                                      else self.env._("This whole workspace (every company)"))

    # ----------------------------------------------------------------- guard
    def _nc_check_editor(self):
        if self.env.su:
            return
        self.env['ncollection.settings.policy']._nc_require('interface_text', 'edit')

    def _nc_record(self):
        self.ensure_one()
        return {'menu': self.menu_id, 'action': self.action_id, 'field': self.field_id, 'help': self.field_id,
                'selection': self.selection_id, 'view': self.view_id}[self.target_type]

    def _nc_column(self):
        return {'menu': 'name', 'action': 'name', 'field': 'field_description', 'help': 'help',
                'selection': 'name', 'view': 'arch_db'}[self.target_type]

    @api.constrains('target_type', 'menu_id', 'action_id', 'field_id', 'selection_id', 'view_id', 'source_term',
                    'value', 'lang')
    def _check_target(self):
        installed = {code for code, _name in self.env['res.lang'].get_installed()}
        for rec in self:
            record = rec._nc_record()
            if not record:
                raise ValidationError(self.env._("Choose the element whose text you change."))
            if rec.lang not in installed:
                raise ValidationError(self.env._("%(lang)s is not an active language.", lang=rec.lang))
            model = (rec.field_id.model or rec.selection_id.field_id.model or rec.action_id.res_model
                     or rec.view_id.model or '')
            if model in PROTECTED_MODELS or (model.startswith('ir.') and rec.target_type != 'menu'):
                raise ValidationError(self.env._("Texts of security and technical settings cannot be changed."))
            if rec.target_type == 'menu' and rec.menu_id._nc_is_technical_menu():
                raise ValidationError(self.env._("Technical menus cannot be renamed."))
            _clean(self.env, rec.value, MAX_HELP if rec.target_type == 'help' else MAX_LABEL)
            if rec.target_type == 'view':
                if not rec.source_term:
                    raise ValidationError(self.env._("Give the text exactly as the screen shows it."))
                if rec.source_term not in rec._nc_view_terms(rec.view_id, rec.lang):
                    raise ValidationError(self.env._(
                        "“%(text)s” is not a text of this screen in this language.", text=rec.source_term))

    @staticmethod
    def _nc_view_terms(view, lang):
        field = view._fields['arch_db']
        value = view.with_context(lang=lang).arch_db or ''
        return set(field.get_trans_terms(value))

    # ------------------------------------------------------------- lifecycle
    @api.model_create_multi
    def create(self, vals_list):
        self._nc_check_editor()
        for vals in vals_list:
            company = vals.get('company_id')
            if company and not self.env.su and company not in self.env.user.company_ids.ids:
                raise AccessError(self.env._("You can only set texts for your own companies."))
            vals['value'] = (vals.get('value') or '').strip()
        records = super().create(vals_list)
        for rec in records:
            rec.sudo().original = rec._nc_current_text()
            rec._nc_log('create', rec.original, rec.value)
        records._nc_signature_reset()
        self.env['ncollection.label.engine'].sudo()._nc_apply_all(force=True)
        return records

    def write(self, vals):
        self._nc_check_editor()
        if set(vals) & {'target_type', 'menu_id', 'action_id', 'field_id', 'selection_id', 'view_id', 'source_term',
                        'lang', 'original'} and not self.env.su:
            raise ValidationError(self.env._("Reset this override and create a new one to change another element."))
        restore = self if vals.get('active') is False else self.browse()
        for rec in self:
            if 'value' in vals and vals['value'] != rec.value:
                rec._nc_log('write', rec.value, vals['value'])
            if rec in restore:
                rec._nc_log('reset', rec.value, rec.original)
        res = super().write(vals)
        restore.sudo()._nc_restore()
        self._nc_signature_reset()
        self.env['ncollection.label.engine'].sudo()._nc_apply_all(force=True)
        return res

    def unlink(self):
        self._nc_check_editor()
        self.sudo()._nc_restore()
        for rec in self:
            rec._nc_log('delete', rec.value, rec.original)
        res = super().unlink()
        self.env['ncollection.label.engine'].sudo()._nc_apply_all(force=True)
        return res

    def action_reset(self):
        """Back to the original (or the platform's) text: the override is archived, its history kept."""
        self.write({'active': False})
        return True

    def _nc_element(self):
        self.ensure_one()
        record = self._nc_record()
        label = dict(TARGETS)[self.target_type]
        detail = self.source_term if self.target_type == 'view' else ''
        return f"{label}: {record.display_name}{' / ' + detail if detail else ''}"[:250]

    def _nc_log(self, event, old, new):
        self.env['ncollection.label.audit'].sudo().create({
            'user_id': self.env.user.id, 'event': event, 'element': self._nc_element(), 'lang': self.lang,
            'old_value': (old or '')[:250], 'new_value': (new or '')[:250]})

    def _nc_signature_reset(self):
        self.env['ir.config_parameter'].sudo().set_param(SIGNATURE_PARAM, '')

    @api.model
    @tools.ormcache('company_id', 'lang')
    def _nc_company_overlay(self, company_id, lang):
        """{('field'|'help', model, name) or ('menu', id): text} of the active company-only overrides."""
        out = {}
        for rec in self.sudo().search([('company_id', '=', company_id), ('lang', '=', lang)]):
            if rec.target_type in ('field', 'help'):
                out[(rec.target_type, rec.field_id.model, rec.field_id.name)] = rec.value
            elif rec.target_type == 'menu':
                out[('menu', rec.menu_id.id)] = rec.value
        return out

    @api.model
    def nc_quick_edit(self, vals):
        """The in-page editor's ONE entry point: find or create (or reset) the override of an element the browser
        identified by its metadata. Validation, rights, policy and audit are the model's own (create / write)."""
        if not isinstance(vals, dict):
            raise ValidationError(self.env._("Malformed request."))
        kind = vals.get('kind')
        lang = vals.get('lang') or self.env.lang or 'en_US'
        company = self.env.company if vals.get('scope') == 'company' else self.env['res.company']
        domain = [('lang', '=', lang), ('company_id', '=', company.id or False)]
        create = {'lang': lang, 'company_id': company.id or False}
        if kind in ('field', 'help'):
            field = self.env['ir.model.fields']._get(str(vals.get('model') or ''), str(vals.get('field') or ''))
            if not field:
                raise ValidationError(self.env._("Unknown field."))
            domain += [('target_type', '=', kind), ('field_id', '=', field.id)]
            create.update(target_type=kind, field_id=field.id)
        elif kind == 'menu':
            menu = self.env['ir.ui.menu'].browse(int(vals.get('menu_id') or 0)).exists()
            if not menu:
                raise ValidationError(self.env._("Unknown menu."))
            domain += [('target_type', '=', 'menu'), ('menu_id', '=', menu.id)]
            create.update(target_type='menu', menu_id=menu.id)
        elif kind == 'view':
            text = str(vals.get('text') or '').strip()
            views = self.env['ir.ui.view'].sudo().search([('model', '=', str(vals.get('model') or ''))])
            view = next((v for v in views if text in self._nc_view_terms(v, lang)), None)
            if not view:
                raise ValidationError(self.env._("This text is not a screen text that can be renamed here."))
            domain += [('target_type', '=', 'view'), ('view_id', '=', view.id), ('source_term', '=', text)]
            create.update(target_type='view', view_id=view.id, source_term=text)
        else:
            raise ValidationError(self.env._("This kind of text cannot be renamed here."))
        existing = self.with_context(active_test=False).search(domain, limit=1)
        if vals.get('reset'):
            if existing.active:
                existing.action_reset()
            return {'reset': True}
        value = vals.get('value')
        if existing:
            existing.write({'value': value, 'active': True})
            return {'id': existing.id}
        return {'id': self.create(dict(create, value=value)).id}

    @api.model
    def nc_can_edit(self):
        """Whether the in-page editor's switch is offered to this user (Interface Text at edit level)."""
        try:
            self.env['ncollection.settings.policy']._nc_require('interface_text', 'edit')
        except AccessError:
            return False
        return self.has_access('create')

    def _nc_current_text(self):
        self.ensure_one()
        record = self._nc_record().with_context(lang=self.lang)
        if self.target_type == 'view':
            return self.source_term
        return record[self._nc_column()] or ''

    def _nc_restore(self):
        """Put back the module's text, then let the engine re-apply the rules (an archived override no longer
        counts)."""
        for rec in self.with_context(active_test=False):
            record = rec._nc_record()
            if not record or rec.company_id:
                continue  # a company-only text never touched the shared translation
            if rec.target_type == 'view':
                record.update_field_translations('arch_db', {rec.lang: {rec.value: rec.source_term}},
                                                 source_lang=rec.lang)
            elif rec.original:
                record.update_field_translations(rec._nc_column(), {rec.lang: rec.original})

    @api.constrains('company_id', 'target_type')
    def _check_company_scope(self):
        for rec in self.filtered('company_id'):
            if rec.target_type not in COMPANY_SCOPED:
                raise ValidationError(self.env._(
                    "Company-only text is available for field labels, field help and menu names; screen texts, page "
                    "titles and choices apply to the whole workspace."))

    def _nc_apply(self):
        self.env.registry.clear_cache()  # the company overlay is cached
        for rec in self.filtered(lambda r: r.active and not r.company_id):
            record = rec._nc_record()
            if not record:
                continue
            if rec.target_type == 'view':
                if rec.source_term in rec._nc_view_terms(record, rec.lang):
                    record.update_field_translations('arch_db', {rec.lang: {rec.source_term: rec.value}},
                                                     source_lang=rec.lang)
            elif (record.with_context(lang=rec.lang)[rec._nc_column()] or '') != rec.value:
                record.update_field_translations(rec._nc_column(), {rec.lang: rec.value})


class LabelEngine(models.AbstractModel):
    """Applies the platform rules, then the tenant overrides, to Odoo's own translated columns.

    The full scans below (search([])) are deliberate: a terminology rule must reach every menu, action, field and
    view; they run only when the signature changed (an install / upgrade), never per request."""
    # pylint: disable=no-search-all
    _name = 'ncollection.label.engine'
    _description = 'Label Engine'

    def _register_hook(self):
        super()._register_hook()
        try:
            with self.env.cr.savepoint():
                self.sudo()._nc_apply_all()
        except Exception:  # never stop a server from starting because of a label
            _logger.exception("Interface texts were not re-applied; the module texts stay in place.")

    def _nc_signature(self):
        self.env.cr.execute(
            "SELECT name, latest_version FROM ir_module_module WHERE state = 'installed' ORDER BY name")
        modules = self.env.cr.fetchall()
        self.env.cr.execute("""SELECT 'r', id, write_date::text FROM ncollection_label_rule WHERE active
                               UNION ALL SELECT 'o', id, write_date::text FROM ncollection_label_override WHERE active
                               ORDER BY 1, 2""")
        return hashlib.sha256(json.dumps([modules, self.env.cr.fetchall()]).encode()).hexdigest()

    @api.model
    def _nc_apply_all(self, force=False):
        Param = self.env['ir.config_parameter'].sudo()
        signature = self._nc_signature()
        if not force and Param.get_param(SIGNATURE_PARAM) == signature:
            return 0
        changed = 0
        installed = {code for code, _name in self.env['res.lang'].get_installed()}
        Rule = self.env['ncollection.label.rule'].sudo()
        for lang in {rule.lang for rule in Rule.search([])} & installed:
            rules = Rule.search([('lang', '=', lang)])
            count = self._nc_apply_rules(rules, lang)
            rules.write({'last_applied': fields.Datetime.now(), 'last_count': count})
            if count:
                self.env['ncollection.label.audit'].create({
                    'user_id': self.env.user.id, 'event': 'rule', 'lang': lang, 'new_value': f"{count} texts",
                    'element': ', '.join(rules.mapped('name'))[:250]})
            changed += count
        self.env['ncollection.label.override'].sudo().search([])._nc_apply()
        self.env.registry.clear_cache()
        self.env.registry.clear_cache('templates')
        Param.set_param(SIGNATURE_PARAM, self._nc_signature())
        return changed

    def _nc_protected(self, model):
        return model in PROTECTED_MODELS or model.startswith('ir.')

    def _nc_apply_rules(self, rules, lang):
        replace = rules._replacer()
        if not replace:
            return 0
        count = 0
        ctx = {'lang': lang, 'active_test': False}
        word = re.compile('|'.join(re.escape(old) for rule in rules for old, _new in rule._pairs()), re.IGNORECASE)

        def simple(records, column, skip=lambda rec: False):
            nonlocal count
            for rec in records.with_context(**ctx):
                if skip(rec):
                    continue
                text = rec[column] or ''
                if text and word.search(text):
                    new = replace(text)
                    if new != text:
                        rec.update_field_translations(column, {lang: new})
                        count += 1

        simple(self.env['ir.ui.menu'].search([]), 'name', skip=lambda m: m._nc_is_technical_menu())
        simple(self.env['ir.actions.act_window'].search([]), 'name',
               skip=lambda a: self._nc_protected(a.res_model or ''))
        candidates = self.env['ir.model.fields'].search([])
        simple(candidates, 'field_description', skip=lambda f: self._nc_protected(f.model))
        simple(candidates.filtered('help'), 'help', skip=lambda f: self._nc_protected(f.model))
        simple(self.env['ir.model.fields.selection'].search([]), 'name',
               skip=lambda s: self._nc_protected(s.field_id.model))
        View = self.env['ir.ui.view'].with_context(**ctx)
        kinds = ('form', 'list', 'kanban', 'search', 'qweb', 'pivot', 'graph', 'calendar', 'activity')
        for view in View.search([('type', 'in', kinds)]):
            if self._nc_protected(view.model or ''):
                continue
            arch = view.arch_db or ''
            if not word.search(arch):
                continue
            terms = {term: replace(term) for term in view._fields['arch_db'].get_trans_terms(arch)}
            terms = {old: new for old, new in terms.items() if new != old}
            if terms:
                view.update_field_translations('arch_db', {lang: terms}, source_lang=lang)
                count += len(terms)
        _logger.info("Terminology (%s): %d texts updated", lang, count)
        return count


class LabelAudit(models.Model):
    """Append-only history of interface-text changes (who, when, which element, old -> new). No one edits or deletes
    it through the ORM; only the label models write it, as superuser."""
    _name = 'ncollection.label.audit'
    _description = 'Interface Text History'
    _order = 'id desc'
    _log_access = True

    user_id = fields.Many2one('res.users', readonly=True, default=lambda self: self.env.user)
    event = fields.Selection([('create', 'Changed'), ('write', 'Edited'), ('reset', 'Reset'), ('delete', 'Deleted'),
                              ('rule', 'Terminology Rule')], readonly=True, required=True)
    element = fields.Char(readonly=True)
    lang = fields.Char(string='Language', readonly=True)
    old_value = fields.Char(string='Before', readonly=True)
    new_value = fields.Char(string='After', readonly=True)

    # Append-only: never written, never deleted (the refusals ARE the behaviour).
    def write(self, vals):  # pylint: disable=method-required-super
        raise AccessError(self.env._("The interface text history cannot be changed."))

    def unlink(self):  # pylint: disable=method-required-super,no-raise-unlink
        message = self.env._("The interface text history cannot be deleted.")
        raise AccessError(message)  # pylint: disable=no-raise-unlink


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    def _nc_is_technical_menu(self):
        """Odoo's technical Settings tree, or a menu only developers / system administrators see."""
        self.ensure_one()
        root = self.env.ref('base.menu_administration', raise_if_not_found=False)
        technical = {self.env.ref('base.group_no_one').id, self.env.ref('base.group_system').id}
        path = [int(part) for part in (self.parent_path or '').split('/') if part]
        return bool((root and root.id in path) or (set(self.group_ids.ids) & technical))


class IrModelFields(models.Model):
    _inherit = 'ir.model.fields'

    @api.model
    def get_field_string(self, model_name):
        """Company-only labels layered on Odoo's (cached) translated labels; a copy, never the cached dict."""
        result = super().get_field_string(model_name)
        overlay = self.env['ncollection.label.override']._nc_company_overlay(self.env.company.id, self.env.lang)
        if overlay:
            result = dict(result)
            for (kind, model, name), value in ((k, v) for k, v in overlay.items() if len(k) == 3):
                if kind == 'field' and model == model_name:
                    result[name] = value
        return result

    @api.model
    def get_field_help(self, model_name):
        result = super().get_field_help(model_name)
        overlay = self.env['ncollection.label.override']._nc_company_overlay(self.env.company.id, self.env.lang)
        if overlay:
            result = dict(result)
            for (kind, model, name), value in ((k, v) for k, v in overlay.items() if len(k) == 3):
                if kind == 'help' and model == model_name:
                    result[name] = value
        return result


class IrUiMenuLabels(models.Model):
    _inherit = 'ir.ui.menu'

    def load_menus(self, debug):
        """Company-only menu names layered on the (uid / lang cached) menu tree; the cached structure is copied."""
        menus = super().load_menus(debug)
        overlay = self.env['ncollection.label.override']._nc_company_overlay(self.env.company.id, self.env.lang)
        renames = {key[1]: value for key, value in overlay.items() if key[0] == 'menu'}
        if not renames:
            return menus
        menus = dict(menus)
        for menu_id, value in renames.items():
            if menu_id in menus:
                menus[menu_id] = dict(menus[menu_id], name=value)
        return menus
