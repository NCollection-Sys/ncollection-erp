# -*- coding: utf-8 -*-
from . import controllers
from . import models
from . import runner_scope


def post_load():
    """Runs only when this module is in ``--load`` (the einvoice-runner container). A no-op without
    NC_EINVOICE_RUNNER=1, so loading it anywhere else changes nothing."""
    runner_scope.install()
