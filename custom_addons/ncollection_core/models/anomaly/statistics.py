# -*- coding: utf-8 -*-
"""Statistical baselines for anomaly detection (P5-T04).

Deliberately **pure Python — no Odoo import**. Two reasons, both practical:

* it can be tested and reasoned about without a database, which is what makes
  the "zero false negatives" acceptance bar checkable at all;
* nothing here can accidentally receive a recordset. The aggregation engine
  flattens its cells precisely so numbers stay numbers, and this layer is where
  that guarantee has to hold.

The ticket says *"statistical baselines first (z-scores/moving averages) — LLM
explanation layered on top only where useful"*. This module is the "first" half
in its entirety; no model here calls out to anything.

A `moving_average` helper was written and then REMOVED: no detector needed it
once the baseline became "mean and spread of the trailing points, excluding the
one under test", which is what a z-score already is. Shipping it unused would
have been dead code carried by tests that proved nothing about the product.

**Why a z-score against a trailing baseline** rather than a fixed threshold:
``ncollection.kpi.threshold`` (P4-T02) already covers "is this number good?"
with configured bands. That answers a different question. A tenant cannot know
in advance what *their* normal weekly sales are, so a fixed band either alerts
constantly or never. A z-score asks "is this unusual **for this tenant**", which
needs no configuration and travels across tenants of wildly different size.
"""

import math

# Below this many points there is no baseline worth the name. Four is the
# smallest number where a mean and a spread both mean something and a single
# outlier cannot BE the baseline; it also keeps a two-week-old tenant quiet
# rather than noisy, which is the failure mode that gets alerting switched off.
MIN_BASELINE_POINTS = 4

# |z| above which a deviation is reported at all. 3.0 is the conventional
# three-sigma line: ~99.7% of a normal distribution sits inside it.
DEFAULT_THRESHOLD = 3.0

# Severity ladder. Deliberately NOT ncollection.kpi.threshold's BAND_STATES
# (good/warning/bad): a band classifies a VALUE and can legitimately be "good",
# whereas an alert only exists because something deviated — there is no "good"
# alert. Sharing the vocabulary would force a meaningless state into one of the
# two models. The escalation points are separated widely enough that a series
# has to be genuinely extreme to reach 'critical'.
SEVERITY_WARNING_AT = 5.0
SEVERITY_CRITICAL_AT = 8.0


def zscore_of_latest(values, min_points=MIN_BASELINE_POINTS):
    """How many standard deviations the LAST value sits from the ones before it.

    Returns ``None`` when there is not enough history to have an opinion.
    ``None`` is not zero and is not an anomaly — every caller must treat it as
    "no opinion", because a young tenant hitting a default of 0.0 would look
    permanently, reassuringly normal.

    The baseline **excludes** the value under test. Including it is a classic
    masking bug: a large enough outlier inflates the very standard deviation it
    is measured against until it stops looking like an outlier. Test
    ``test_baseline_excludes_the_point_under_test`` pins this.

    A zero-variance baseline (a perfectly flat series) is the divide-by-zero
    trap. It is handled rather than guarded away, because it is also the
    clearest signal there is:

    * flat and still flat            -> ``0.0``       (nothing happened)
    * flat and then it moved         -> ``±inf``      (maximally surprising)

    Returning ``inf`` rather than a large finite number keeps the ordering
    honest — no genuine finite deviation should ever outrank "a constant
    changed" — and ``is_anomalous`` compares it correctly.
    """
    if not values or len(values) < min_points:
        return None

    baseline = values[:-1]
    latest = values[-1]
    mean = sum(baseline) / float(len(baseline))

    # Population standard deviation over the baseline. Population rather than
    # sample: the baseline IS the full history we are judging against, not a
    # sample drawn from something larger.
    variance = sum((point - mean) ** 2 for point in baseline) / float(len(baseline))
    stddev = math.sqrt(variance)

    if stddev == 0.0:
        if latest == mean:
            return 0.0
        return math.copysign(float('inf'), latest - mean)

    return (latest - mean) / stddev


def is_anomalous(zscore, threshold=DEFAULT_THRESHOLD):
    """True when a z-score is far enough from zero to report.

    ``None`` is False — "no opinion" must never raise an alert. That single
    line is the difference between a quiet new tenant and a tenant whose first
    experience of the product is a page of spurious warnings.
    """
    if zscore is None:
        return False
    return abs(zscore) >= threshold


def severity_for(zscore, threshold=DEFAULT_THRESHOLD):
    """Map a z-score onto ``info`` / ``warning`` / ``critical``, or ``None``.

    ``None`` means "not worth an alert" and is returned for both a small
    deviation and for no opinion at all, so callers have exactly one thing to
    check before creating a record.
    """
    if not is_anomalous(zscore, threshold):
        return None
    magnitude = abs(zscore)
    if magnitude >= SEVERITY_CRITICAL_AT:
        return 'critical'
    if magnitude >= SEVERITY_WARNING_AT:
        return 'warning'
    return 'info'
