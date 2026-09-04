# -*- coding: utf-8 -*-
from . import models
# #472: re-exported so the manifest's 'post_init_hook' resolves — Odoo looks it
# up in the module's own namespace, not in models/.
from .models.res_users import post_init_hook
