# -*- coding: utf-8 -*-
"""Arabic shaping and bidirectional ordering for text drawn straight onto a PDF, with no third-party library.

WHY THIS EXISTS. A cheque is placed on the page by millimetre with ``reportlab``; ``reportlab`` draws the glyphs it is
told to draw, left to right, one code point each. Arabic needs two things before that is correct: SHAPING (each letter
has isolated / final / initial / medial forms and ``لا`` is a ligature) and ORDERING (right-to-left runs, with Latin
words and numbers inside them kept left to right). The Odoo image carries neither ``arabic_reshaper`` nor
``python-bidi``, and adding a pip dependency to the tenant image is an architecture decision this module should not
make on its own. HTML rendering (wkhtmltopdf) does both, but it is not an exact-size, pixel-free path, which is
the whole point of a cheque.

The letter forms are not typed in here. Unicode already records them: every Arabic Presentation Form is decomposed to
its base letter and tagged ``<isolated>`` / ``<final>`` / ``<initial>`` / ``<medial>`` in ``unicodedata``. The table
is built from that at import, so there is no hand-copied letter table to get wrong.

SCOPE, stated plainly. This is correct for what appears on a cheque: names, amounts in words, dates, references, digits
and short Latin fragments in one line. It is not a general Unicode bidi implementation (no embeddings or isolates), and
it drops Arabic diacritics (harakat) because without a layout engine they cannot be positioned. ``prepare`` is the only
entry point a caller needs.
"""
import unicodedata

_LAM = 'ل'
_TATWEEL = 'ـ'
_ALEF_FORMS = ('آ', 'أ', 'إ', 'ا')
_LAM_ALEF = ('0622', '0623', '0625', '0627')         # the four alef letters lam joins into a ligature with
_MARKS = set(chr(c) for c in range(0x064B, 0x0653)) | {'ٰ'}      # harakat: dropped (no layout engine to place them)

_FORMS = {}            # base letter -> {'isolated': ch, 'final': ch, 'initial': ch, 'medial': ch}
_LIGATURES = {}        # (lam, alef variant) -> {'isolated': ch, 'final': ch}


def _build_tables():
    for code in list(range(0xFB50, 0xFDFF)) + list(range(0xFE70, 0xFF00)):
        ch = chr(code)
        decomposition = unicodedata.decomposition(ch)
        if not decomposition.startswith('<'):
            continue
        tag, _, rest = decomposition[1:].partition('> ')
        if tag not in ('isolated', 'final', 'initial', 'medial'):
            continue
        parts = rest.split()
        if len(parts) == 1:
            _FORMS.setdefault(chr(int(parts[0], 16)), {}).setdefault(tag, ch)
        elif len(parts) == 2 and parts[0] == '0644' and parts[1] in _LAM_ALEF:
            # ONLY lam + alef is a mandatory ligature. The Presentation Forms-A block also records lam + meem, lam +
            # jeem ... as ligatures, but those are optional and most fonts (DejaVu included) have no glyph for them:
            # shaping them printed an empty box where two letters belonged.
            _LIGATURES.setdefault((_LAM, chr(int(parts[1], 16))), {}).setdefault(tag, ch)


_build_tables()


def _joins_next(ch):
    """Can ``ch`` connect to the letter AFTER it (a dual-joining letter has an initial form)."""
    return ch == _TATWEEL or 'initial' in _FORMS.get(ch, ())


def _joins_prev(ch):
    """Can ``ch`` connect to the letter BEFORE it."""
    return ch == _TATWEEL or 'final' in _FORMS.get(ch, ())


def shape(text):
    """``text`` with every Arabic letter replaced by its contextual presentation form. Logical order is kept."""
    chars = [c for c in text if c not in _MARKS]
    out = []
    i = 0
    while i < len(chars):
        ch = chars[i]
        prev_joins = bool(out) and i > 0 and _joins_next(chars[i - 1])
        if ch == _LAM and i + 1 < len(chars) and (_LAM, chars[i + 1]) in _LIGATURES:
            forms = _LIGATURES[(_LAM, chars[i + 1])]
            out.append(forms.get('final') if prev_joins and 'final' in forms else forms.get('isolated', ch))
            i += 2
            continue
        forms = _FORMS.get(ch)
        if not forms:
            out.append(ch)
            i += 1
            continue
        next_joins = (i + 1 < len(chars) and _joins_next(ch) and _joins_prev(chars[i + 1]))
        if prev_joins and next_joins and 'medial' in forms:
            out.append(forms['medial'])
        elif prev_joins and 'final' in forms:
            out.append(forms['final'])
        elif next_joins and 'initial' in forms:
            out.append(forms['initial'])
        else:
            out.append(forms.get('isolated', ch))
        i += 1
    return ''.join(out)


# ---------------------------------------------------------------------------------------------- bidi
_OPENING = {'(': ')', '[': ']', '{': '}'}
_CLOSING = {')', ']', '}'}
_MIRROR = {'(': ')', ')': '(', '[': ']', ']': '[', '{': '}', '}': '{', '<': '>', '>': '<',
           '«': '»', '»': '«'}


def _kind(ch):
    """'R', 'L', 'N' (a number) or 'O' (neutral), from Unicode's own bidirectional class."""
    cls = unicodedata.bidirectional(ch)
    if cls in ('R', 'AL'):
        return 'R'
    if cls == 'L':
        return 'L'
    if cls in ('EN', 'AN'):
        return 'N'
    return 'O'


def has_rtl(text):
    return any(_kind(c) == 'R' for c in text)


def base_direction(text, direction='auto'):
    """'rtl' or 'ltr': the paragraph direction of ``text`` (an explicit direction wins; 'auto' follows the first strong
    letter, as the bidirectional algorithm does)."""
    if direction in ('ltr', 'rtl'):
        return direction
    first = next((k for k in (_kind(c) for c in text or '') if k in ('R', 'L')), 'L')
    return 'rtl' if first == 'R' else 'ltr'


def reorder(text, base='auto'):
    """``text`` (already shaped, logical order) in VISUAL left-to-right order for drawing.

    ``base`` is 'ltr', 'rtl' or 'auto' (the first strong letter decides)."""
    if not text:
        return text
    kinds = [_kind(c) for c in text]
    if base == 'auto':
        first = next((k for k in kinds if k in ('R', 'L')), 'L')
        base = 'rtl' if first == 'R' else 'ltr'
    base_level = 1 if base == 'rtl' else 0
    base_kind = 'R' if base == 'rtl' else 'L'

    # separators glued to digits belong to the number ("3,100.00", "28/09/2026", "-5", "%")
    n = len(text)
    for i, ch in enumerate(text):
        if kinds[i] == 'O' and ch in ',.:/-+%#$':
            left = i > 0 and kinds[i - 1] == 'N'
            right = i + 1 < n and kinds[i + 1] == 'N'
            if (left and right) or (ch in '%#$' and (left or right)) or (ch in '+-' and right):
                kinds[i] = 'N'

    # a number that follows Latin text is part of that Latin text (UBA W7): "INV-2026-0042 <arabic word>" must keep
    # the space next to the number, which only happens when the number reads as left-to-right like the letters
    # before it. A number after Arabic (or with nothing before it in a right-to-left line) stays a number of its own.
    last_strong = 'R' if base == 'rtl' else 'L'
    for i, kind in enumerate(kinds):
        if kind in ('L', 'R'):
            last_strong = kind
        elif kind == 'N' and last_strong == 'L':
            kinds[i] = 'L'

    # a matching pair of brackets takes the direction of what is inside it (UBA N0), so "(<arabic words>)" stays one
    # unit even in a left-to-right line instead of its closing bracket drifting to the end of the line
    stack = []
    for i, ch in enumerate(text):
        if ch in _OPENING:
            stack.append((i, _OPENING[ch]))
        elif ch in _CLOSING and stack:
            for depth in range(len(stack) - 1, -1, -1):
                if stack[depth][1] == ch:
                    start = stack[depth][0]
                    del stack[depth:]
                    inside = {'R' if k in ('R', 'N') else 'L' for k in kinds[start + 1:i] if k in ('L', 'R', 'N')}
                    if inside:
                        before = next(
                            (kinds[j] for j in range(start - 1, -1, -1) if kinds[j] in ('L', 'R')), base_kind)
                        direction = (base_kind if base_kind in inside else before if before in inside
                                     else sorted(inside)[0])
                        kinds[start] = kinds[i] = direction
                    break

    # neutrals take the direction on both sides when it agrees, otherwise the paragraph direction (UBA N1/N2);
    # numbers influence a neutral next to them as if they were right-to-left letters (N1)
    def side(k):
        return 'R' if k in ('R', 'N') else 'L'

    resolved = list(kinds)
    for i, k in enumerate(kinds):
        if k != 'O':
            continue
        j = i - 1
        while j >= 0 and kinds[j] == 'O':
            j -= 1
        m = i + 1
        while m < n and kinds[m] == 'O':
            m += 1
        before = side(kinds[j]) if j >= 0 else base_kind
        after = side(kinds[m]) if m < n else base_kind
        resolved[i] = before if before == after else base_kind

    levels = []
    for k in resolved:
        if k == 'R':
            levels.append(1)
        elif k == 'N':      # a number after Arabic: its own left-to-right run inside the right-to-left one (UBA I2)
            levels.append(2)
        else:               # Latin letters read left to right: level 2 inside an RTL paragraph, 0 in an LTR one
            levels.append(2 if base_level == 1 else 0)
    chars = list(text)
    # mirror brackets inside right-to-left runs
    for i, level in enumerate(levels):
        if level % 2 == 1 and chars[i] in _MIRROR:
            chars[i] = _MIRROR[chars[i]]
    # L2: from the highest level down to the lowest odd level, reverse every run at that level or above
    highest = max(levels)
    lowest_odd = min((lv for lv in levels if lv % 2 == 1), default=None)
    if lowest_odd is None:
        return ''.join(chars)
    for level in range(highest, lowest_odd - 1, -1):
        i = 0
        while i < n:
            if levels[i] >= level:
                j = i
                while j < n and levels[j] >= level:
                    j += 1
                chars[i:j] = chars[i:j][::-1]
                levels[i:j] = levels[i:j][::-1]
                i = j
            else:
                i += 1
    return ''.join(chars)


def prepare(text, direction='auto'):
    """The string to hand to the PDF canvas: shaped, then put in visual order. Plain Latin text comes back unchanged
    (nothing in it needs shaping or reordering)."""
    text = '' if text is None else str(text)
    if not text:
        return text
    if direction == 'ltr' and not has_rtl(text):
        return text
    if direction == 'auto' and not has_rtl(text):
        return text
    return reorder(shape(text), direction)


def to_arabic_indic(text):
    """Western digits as Arabic-Indic digits (٠١٢٣٤٥٦٧٨٩), for a field configured that way."""
    return text.translate({ord('0') + i: ord('٠') + i for i in range(10)})
