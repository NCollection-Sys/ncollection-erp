# -*- coding: utf-8 -*-
"""The onboarding messages the system user already posted in Discuss speak in the system's name.

From this version the system user's messages are worded with the system's name when they are created, and re-worded
when the system is renamed (models/mail_thread.py). The ones posted before ("Odoo's chat helps employees...",
"@OdooBot") are still in every user's chat with the bot, so they get the same wording here, through the ORM, and the
bot's picture becomes the system's icon. Idempotent.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    env['mail.message']._nc_reword_system_messages()
    env['ncollection.vendor.content']._nc_sync_bot_avatar()
