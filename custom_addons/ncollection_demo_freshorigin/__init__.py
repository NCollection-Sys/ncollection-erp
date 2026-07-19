from . import hooks


def _post_init_hook(env):
    hooks._create_demo_stock(env)
