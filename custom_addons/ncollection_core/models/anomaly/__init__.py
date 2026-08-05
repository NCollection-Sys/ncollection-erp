# -*- coding: utf-8 -*-
# P5-T04 anomaly detection.
#
# `statistics` is pure Python and imports no Odoo, so it stays importable (and
# testable) on its own — see its module docstring. `detectors` must be imported
# before `alert`, which reads DETECTOR_KEYS from it.
from . import statistics
from . import detectors
from . import alert
