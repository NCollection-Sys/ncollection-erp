from . import models
from . import wizard
from . import i18n_loader


def post_load():
    i18n_loader.install()
