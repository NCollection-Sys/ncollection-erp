# -*- coding: utf-8 -*-
def post_init_hook(env):
    """Existing lines keep their discount: the new "as entered" percentage starts equal to it (percentage mode)."""
    env.cr.execute("UPDATE account_move_line SET nc_discount_percent = COALESCE(discount, 0) WHERE nc_discount_percent IS NULL OR nc_discount_percent = 0")
