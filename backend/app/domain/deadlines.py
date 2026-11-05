"""Deterministic deadline / event-time normalization.

The language model only *quotes* the deadline wording. This module turns that
wording into a due instant, a due date, or an explicit "unknown", with every
assumption written down. Hard rules:

* Never invent a clock time for a date-only deadline (no fake 11:59 PM).
* Never manufacture a UTC instant when the timezone is unknown.
* Relative "within N hours" is elapsed time from the provider timestamp, so it
  is correct across DST changes.
* Business-day arithmetic always requests review (holiday calendars differ).
* Nonexistent / ambiguous local times (DST gaps/overlaps) request review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("America/New_York")

_TZ_ALIASES: dict[str, tuple[str, str | None]] = {
    # token -> (IANA zone, the DST-ness implied by the abbreviation or None)
    "et": ("America/New_York", None),
    "eastern": ("America/New_York", None),
    "eastern time": ("America/New_York", None),
    "est": ("America/New_York", "standard"),
    "edt": ("America/New_York", "daylight"),
    "ct": ("America/Chicago", None),
    "central": ("America/Chicago", None),
    "central time": ("America/Chicago", None),
    "cst": ("America/Chicago", "standard"),
    "cdt": ("America/Chicago", "daylight"),
    "mt": ("America/Denver", None),
    "mountain": ("America/Denver", None),
    "mst": ("America/Denver", "standard"),
    "mdt": ("America/Denver", "daylight"),
    "pt": ("America/Los_Angeles", None),
    "pacific": ("America/Los_Angeles", None),
    "pacific time": ("America/Los_Angeles", None),
    "pst": ("America/Los_Angeles", "standard"),
    "pdt": ("America/Los_Angeles", "daylight"),
    "utc": ("UTC", None),
    "gmt": ("UTC", None),
    "z": ("UTC", None),
    "bst": ("Europe/London", "daylight"),
    "ist": ("Asia/Kolkata", None),
    "cet": ("Europe/Berlin", "standard"),
    "cest": ("Europe/Berlin", "daylight"),
}
_TZ_TOKEN_RE = (
    r"(?P<tz>(?:eastern|central|mountain|pacific)(?:\s+(?:standard|daylight))?(?:\s+time)?"
    r"|[ecmp][sd]?t|utc|gmt|bst|ist|cest|cet"
    r"|(?:utc|gmt)\s*[+-]\s*\d{1,2}(?::?\d{2})?"
    r"|[+-]\d{2}:?\d{2}"
    r"|[A-Za-z]+/[A-Za-z_]+)"
)

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for m in names
}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}
_NUM_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fourteen": 14, "twenty-four": 24,
    "forty-eight": 48, "seventy-two": 72, "a": 1, "an": 1,
}
_NUM = r"(?P<n>\d{1,3}|" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True)) + r")"

_MONTH_NAME = r"(?P<mon>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"
_DATE_MDY_NAME = re.compile(
    r"\b(?:(?P<wd>mon|tue|tues|wed|thu|thurs|fri|sat|sun)[a-z]*\.?,?\s+)?"
    + _MONTH_NAME
    + r"\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
_DATE_DMY_NAME = re.compile(
    r"\b(?P<day>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MONTH_NAME + r"(?:,?\s*(?P<year>20\d{2}))?\b",
    re.IGNORECASE,
)
_DATE_ISO = re.compile(r"\b(?P<year>20\d{2})-(?P<month>\d{2})-(?P<day>\d{2})\b")
_DATE_US = re.compile(r"\b(?P<month>\d{1,2})/(?P<day>\d{1,2})(?:/(?P<year>(?:20)?\d{2}))?\b")
_WEEKDAY_ONLY = re.compile(
    r"\b(?:by|before|on|until|this|next)\s+(?P<wd>monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"\b(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)(?![a-z])"
    r"|\b(?P<h24>[01]?\d|2[0-3]):(?P<m24>[0-5]\d)\b"
    r"|\b(?P<word>noon|midnight|end of (?:the )?day|eod|close of business|cob)\b",
    re.IGNORECASE,
)
_TZ_AFTER_TIME = re.compile(r"^\s*\(?\s*" + _TZ_TOKEN_RE + r"\b\)?", re.IGNORECASE)
_TZ_ANYWHERE = re.compile(r"(?<![A-Za-z])" + _TZ_TOKEN_RE + r"(?![A-Za-z])", re.IGNORECASE)

_RELATIVE = re.compile(
    r"\b(?:within|in|next|no later than|up to|you have|have)\s+(?:the\s+next\s+)?" + _NUM
    + r"\s*(?P<bus>business\s+|working\s+|calendar\s+)?(?P<unit>hours?|hrs?|minutes?|mins?|days?|weeks?)\b"
    r"(?P<tail>[^.;\n]{0,80})",
    re.IGNORECASE,
)
_OPENED_ANCHOR = re.compile(
    r"\b(?:of|after|from|once)\s+(?:you\s+)?(?:start|begin|open|launch|click|access)",
    re.IGNORECASE,
)
_SENT_ANCHOR = re.compile(r"\b(?:sent|was sent|sending)\b", re.IGNORECASE)


@dataclass
class DeadlineResult:
    raw: str | None
    due_at: datetime | None = None  # aware UTC, only when reliable
    due_date: str | None = None  # YYYY-MM-DD when only the day is known
    due_timezone: str | None = None  # IANA zone the deadline was stated in
    precision: str = "unknown"  # instant|day|relative|unknown
    anchor: str | None = None
    assumptions: list[str] = field(default_factory=list)
    needs_review: bool = False
    confidence: float = 0.0
    kind: str = "unknown"

    def as_dict(self) -> dict:
        return {
            "raw": self.raw,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "due_date": self.due_date,
            "due_timezone": self.due_timezone,
            "precision": self.precision,
            "anchor": self.anchor,
            "assumptions": list(self.assumptions),
            "needs_review": self.needs_review,
            "confidence": self.confidence,
            "kind": self.kind,
        }


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------


def resolve_timezone(token: str | None) -> tuple[ZoneInfo | timezone | None, str | None, str | None]:
    """Return (tzinfo, IANA-ish name, implied dst kind) for a timezone token."""
    if not token:
        return None, None, None
    t = re.sub(r"\s+", " ", token.strip().strip("()").lower())
    t = re.sub(r"\b(standard|daylight)\b", lambda m: m.group(1), t)
    if t in {"eastern standard time", "eastern standard"}:
        return ZoneInfo("America/New_York"), "America/New_York", "standard"
    if t in {"eastern daylight time", "eastern daylight"}:
        return ZoneInfo("America/New_York"), "America/New_York", "daylight"
    for region, zone in (
        ("central", "America/Chicago"),
        ("mountain", "America/Denver"),
        ("pacific", "America/Los_Angeles"),
    ):
        if t.startswith(region):
            kind = "standard" if "standard" in t else "daylight" if "daylight" in t else None
            return ZoneInfo(zone), zone, kind
    if t in _TZ_ALIASES:
        zone, kind = _TZ_ALIASES[t]
        return ZoneInfo(zone), zone, kind
    m = re.fullmatch(r"(?:utc|gmt)?\s*([+-])\s*(\d{1,2}):?(\d{2})?", t)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
        return timezone(sign * delta), f"UTC{m.group(1)}{int(m.group(2)):02d}:{m.group(3) or '00'}", None
    if "/" in token:
        try:
            return ZoneInfo(token.strip()), token.strip(), None
        except Exception:  # noqa: BLE001
            return None, None, None
    return None, None, None


def localize(naive: datetime, tz: ZoneInfo | timezone) -> tuple[datetime | None, str | None]:
    """Attach tz to a wall-clock time, refusing DST gaps and overlaps.

    Returns (aware datetime or None, problem description or None).
    """
    if isinstance(tz, timezone):
        return naive.replace(tzinfo=tz), None
    a = naive.replace(tzinfo=tz, fold=0)
    b = naive.replace(tzinfo=tz, fold=1)
    roundtrip = a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    if roundtrip != naive:
        return None, f"{naive:%Y-%m-%d %H:%M} does not exist in {tz.key} (DST spring-forward gap)"
    if a.utcoffset() != b.utcoffset():
        return None, f"{naive:%Y-%m-%d %H:%M} occurs twice in {tz.key} (DST fall-back overlap)"
    return a, None


# ---------------------------------------------------------------------------
# US federal holidays (only used to *disclose* ambiguity, never to decide)
# ---------------------------------------------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def us_federal_holidays(year: int) -> dict[date, str]:
    return {
        date(year, 1, 1): "New Year's Day",
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents' Day",
        _last_weekday(year, 5, 0): "Memorial Day",
        date(year, 6, 19): "Juneteenth",
        date(year, 7, 4): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 10, 0, 2): "Indigenous Peoples' / Columbus Day",
        date(year, 11, 11): "Veterans Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        date(year, 12, 25): "Christmas Day",
    }


def add_business_days(start: date, n: int) -> tuple[date, list[str]]:
    """Skip weekends only. Returns (date, holidays found in the window)."""
    d = start
    added = 0
    hits: list[str] = []
    while added < n:
        d += timedelta(days=1)
        if d.weekday() >= 5:
            continue
        added += 1
    cursor = start + timedelta(days=1)
    while cursor <= d:
        for hday, name in us_federal_holidays(cursor.year).items():
            if hday == cursor:
                hits.append(f"{name} ({cursor.isoformat()})")
        cursor += timedelta(days=1)
    return d, hits


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


@dataclass
class _DateHit:
    value: date
    year_inferred: bool
    end: int
    start: int


def _infer_year(month: int, day: int, reference: datetime | None) -> tuple[int | None, bool]:
    if reference is None:
        return None, True
    ref = reference.astimezone(DISPLAY_TZ).date()
    for year in (ref.year, ref.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        # A deadline is normally after the email; allow a week of slack for odd quoting.
        if candidate >= ref - timedelta(days=7):
            return year, True
    return None, True


def find_date(text: str, reference: datetime | None) -> _DateHit | None:
    for pat, kind in ((_DATE_ISO, "iso"), (_DATE_MDY_NAME, "name"), (_DATE_DMY_NAME, "name"), (_DATE_US, "us")):
        m = pat.search(text)
        if not m:
            continue
        try:
            if kind == "name":
                month = _MONTHS[m.group("mon").lower().rstrip(".")]
            else:
                month = int(m.group("month"))
            day = int(m.group("day"))
            year_txt = m.group("year")
            inferred = False
            if year_txt:
                year = int(year_txt) if len(year_txt) == 4 else 2000 + int(year_txt)
            else:
                year, inferred = _infer_year(month, day, reference)
                if year is None:
                    continue
            return _DateHit(date(year, month, day), inferred, m.end(), m.start())
        except (ValueError, KeyError):
            continue
    return None


def _parse_time(m: re.Match) -> tuple[time | None, str | None]:
    """Return (time, special) where special in {'midnight','eod'}."""
    word = (m.group("word") or "").lower()
    if word == "noon":
        return time(12, 0), None
    if word == "midnight":
        return None, "midnight"
    if word:
        return None, "eod"
    if m.group("h24") is not None:
        return time(int(m.group("h24")), int(m.group("m24"))), None
    h = int(m.group("h"))
    minute = int(m.group("m") or 0)
    if not (1 <= h <= 12) or minute > 59:
        return None, None
    pm = m.group("ampm").lower().startswith("p")
    if h == 12:
        h = 0
    if pm:
        h += 12
    return time(h, minute), None


def _find_tz(text: str, after: int | None = None) -> str | None:
    if after is not None:
        m = _TZ_AFTER_TIME.match(text[after:])
        if m:
            return m.group("tz")
    m = _TZ_ANYWHERE.search(text)
    if m:
        tok = m.group("tz")
        # "at" / "pt" collisions: only accept bare 2-letter tokens when uppercase in source.
        if len(tok) == 2 and not tok.isupper():
            return None
        if len(tok) == 3 and tok.lower() in {"est", "edt", "cst", "cdt", "mst", "mdt", "pst", "pdt", "utc", "gmt", "bst", "ist", "cet"} and not tok.isupper():
            return None
        return tok
    return None


def _num(token: str) -> int:
    t = token.lower()
    return int(t) if t.isdigit() else _NUM_WORDS[t]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_deadline(
    raw: str | None,
    *,
    received_at: datetime | None,
    sent_at: datetime | None = None,
    timezone_hint: str | None = None,
    date_only_hint: str | None = None,
    anchor_hint: str | None = None,
) -> DeadlineResult:
    """Interpret one deadline expression. Pure function; never raises on bad input."""
    res = DeadlineResult(raw=(raw or None))
    text = (raw or "").strip()
    if not text and not date_only_hint:
        res.kind = "none"
        return res

    # 1) Relative ("within 72 hours of receiving this email")
    rel = _RELATIVE.search(text)
    if rel:
        return _relative(res, rel, received_at=received_at, sent_at=sent_at, anchor_hint=anchor_hint)

    # 2) Absolute date (+ optional time + timezone)
    hit = find_date(text, received_at or sent_at)
    weekday_only = None if hit else _WEEKDAY_ONLY.search(text)
    if hit is None and weekday_only is not None and received_at is not None:
        target = _WEEKDAYS[weekday_only.group("wd").lower()]
        ref = received_at.astimezone(DISPLAY_TZ).date()
        delta = (target - ref.weekday()) % 7 or 7
        hit = _DateHit(ref + timedelta(days=delta), True, weekday_only.end(), weekday_only.start())
        res.assumptions.append(
            f"Only a weekday was given; interpreted as the next {weekday_only.group('wd').title()} "
            f"after the email ({hit.value.isoformat()})."
        )
        res.needs_review = True
    if hit is None and date_only_hint:
        try:
            hit = _DateHit(date.fromisoformat(date_only_hint), False, len(text), 0)
            res.assumptions.append("Date taken from the model's date_only field.")
        except ValueError:
            hit = None
    if hit is None:
        if re.search(r"\b(today|tonight|tomorrow)\b", text, re.IGNORECASE) and received_at:
            ref = received_at.astimezone(DISPLAY_TZ).date()
            offs = 1 if re.search(r"tomorrow", text, re.IGNORECASE) else 0
            hit = _DateHit(ref + timedelta(days=offs), True, len(text), 0)
            res.assumptions.append(
                "Relative day word interpreted against the email's received date in America/New_York."
            )
            res.needs_review = True
        else:
            res.kind = "unknown"
            res.needs_review = True
            res.assumptions.append("No date or duration could be read from the deadline wording.")
            return res

    if hit.year_inferred and not weekday_only:
        res.assumptions.append(f"No year stated; inferred {hit.value.year} from the email date.")
        res.confidence = 0.8

    tm = None
    for m in _TIME.finditer(text):
        tm = m
        break
    tz_token = None
    if tm is not None:
        tz_token = _find_tz(text, after=tm.end())
    if tz_token is None:
        tz_token = _find_tz(text)
    if tz_token is None and timezone_hint:
        tz_token = timezone_hint
        res.assumptions.append(f"Timezone '{timezone_hint}' taken from elsewhere in the email.")

    tzinfo, tzname, dst_kind = resolve_timezone(tz_token)
    res.due_timezone = tzname

    if tm is None:
        # Date only: keep it a date. Do not invent a time or a UTC instant.
        res.kind = "date_only"
        res.precision = "day"
        res.due_date = hit.value.isoformat()
        res.assumptions.append("No time of day stated; kept as a date-only deadline.")
        res.confidence = max(res.confidence, 0.85) if res.confidence == 0 else res.confidence
        return res

    clock, special = _parse_time(tm)
    res.kind = "absolute"
    the_day = hit.value
    if special == "midnight":
        res.assumptions.append(
            "'Midnight' is ambiguous (start vs. end of the day); interpreted as the end of "
            f"{the_day.isoformat()} (00:00 the next day)."
        )
        res.needs_review = True
        clock = time(0, 0)
        the_day = the_day + timedelta(days=1)
    elif special == "eod":
        res.assumptions.append("'End of day / close of business' has no defined clock time.")
        res.precision = "day"
        res.due_date = hit.value.isoformat()
        res.needs_review = True
        res.confidence = 0.6
        return res
    if clock is None:
        res.precision = "day"
        res.due_date = hit.value.isoformat()
        res.needs_review = True
        res.assumptions.append("Time of day could not be read.")
        return res

    if tzinfo is None:
        res.precision = "day"
        res.due_date = hit.value.isoformat()
        res.needs_review = True
        res.assumptions.append(
            f"A time ({clock:%H:%M}) was given without a timezone; no UTC instant was computed."
        )
        res.confidence = 0.5
        return res

    local, problem = localize(datetime.combine(the_day, clock), tzinfo)
    if local is None:
        res.precision = "day"
        res.due_date = hit.value.isoformat()
        res.needs_review = True
        res.assumptions.append(problem or "Local time could not be resolved.")
        res.confidence = 0.4
        return res

    if dst_kind and isinstance(tzinfo, ZoneInfo):
        is_dst = bool(local.dst())
        if (dst_kind == "standard") == is_dst:
            res.assumptions.append(
                f"'{tz_token}' names {dst_kind} time but {the_day.isoformat()} falls in "
                f"{'daylight' if is_dst else 'standard'} time; used the zone's actual offset "
                f"({tzname})."
            )
            res.confidence = min(res.confidence or 0.8, 0.8)
    res.due_at = local.astimezone(timezone.utc)
    res.precision = "instant"
    if res.confidence == 0:
        res.confidence = 0.95 if not res.assumptions else 0.8
    return res


def _relative(
    res: DeadlineResult,
    m: re.Match,
    *,
    received_at: datetime | None,
    sent_at: datetime | None,
    anchor_hint: str | None,
) -> DeadlineResult:
    n = _num(m.group("n"))
    unit = m.group("unit").lower()
    business = (m.group("bus") or "").strip().lower()
    tail = m.group("tail") or ""
    res.kind = "relative"
    res.precision = "relative"

    if _OPENED_ANCHOR.search(tail):
        res.anchor = "invitation_opened"
        res.needs_review = True
        res.assumptions.append(
            "The time limit starts when you open/start the assessment, so it is not a calendar deadline."
        )
        res.confidence = 0.7
        return res

    use_sent = bool(_SENT_ANCHOR.search(tail)) or anchor_hint == "email_sent_at"
    anchor_dt = sent_at if use_sent else received_at
    res.anchor = "email_sent_at" if use_sent else "email_received_at"
    if anchor_dt is None:
        res.needs_review = True
        res.assumptions.append(f"Anchor timestamp ({res.anchor}) is unavailable.")
        return res
    if not tail.strip() or not re.search(r"receiv|receipt|this (?:e-?mail|message|invitation)|sent|today", tail, re.IGNORECASE):
        res.assumptions.append(
            "The wording does not name a start point; counted from when the email was received."
        )

    if business.startswith(("business", "working")):
        local = anchor_dt.astimezone(DISPLAY_TZ)
        end_day, holidays = add_business_days(local.date(), n)
        res.kind = "business_days"
        res.precision = "day"
        res.due_date = end_day.isoformat()
        res.due_timezone = "America/New_York"
        res.needs_review = True
        res.assumptions.append(
            f"Counted {n} business day(s) from {local.date().isoformat()} skipping weekends only; "
            "the employer's holiday calendar and cut-off time are unknown."
        )
        if holidays:
            res.assumptions.append("US holidays inside the window: " + ", ".join(holidays) + ".")
        res.confidence = 0.6
        return res

    if unit.startswith(("min",)):
        res.due_at = anchor_dt.astimezone(timezone.utc) + timedelta(minutes=n)
        res.confidence = 0.9
        return res
    if unit.startswith(("hour", "hr")):
        res.due_at = (anchor_dt.astimezone(timezone.utc) + timedelta(hours=n))
        res.confidence = 0.95
        return res

    days = n * 7 if unit.startswith("week") else n
    # Calendar days at the same Eastern wall-clock time; disclose a DST difference.
    local = anchor_dt.astimezone(DISPLAY_TZ)
    wall = (local.replace(tzinfo=None) + timedelta(days=days))
    aware, problem = localize(wall, DISPLAY_TZ)
    elapsed = anchor_dt.astimezone(timezone.utc) + timedelta(hours=24 * days)
    if aware is None:
        res.due_at = elapsed
        res.assumptions.append(f"{problem}; used {24 * days} elapsed hours instead.")
        res.needs_review = True
        res.confidence = 0.7
        return res
    res.due_at = aware.astimezone(timezone.utc)
    if res.due_at != elapsed:
        res.assumptions.append(
            f"'{n} {unit}' crosses a DST change: computed as {days} calendar day(s) at the same "
            f"Eastern clock time; {24 * days} elapsed hours would be "
            f"{elapsed.astimezone(DISPLAY_TZ):%Y-%m-%d %H:%M %Z}."
        )
        res.confidence = 0.8
    else:
        res.confidence = 0.9
    return res


def parse_event_time(
    raw: str | None, *, received_at: datetime | None, timezone_hint: str | None = None
) -> DeadlineResult:
    """Interview / event start times use the same absolute rules."""
    return normalize_deadline(raw, received_at=received_at, timezone_hint=timezone_hint)


def find_deadline_phrases(text: str) -> list[str]:
    """Deterministic scan for deadline wording (used to cross-check the model)."""
    phrases: list[str] = []
    for m in re.finditer(
        r"[^.\n]*\b(?:within\s+\w+\s+(?:business\s+)?(?:hours?|days?)|deadline|due\s+(?:by|on)|"
        r"no later than|complete (?:it |this |the assessment )?by|expires?(?: on)?|before)\b[^.\n]*",
        text,
        re.IGNORECASE,
    ):
        frag = m.group(0).strip()
        if 8 <= len(frag) <= 300:
            phrases.append(frag)
    return phrases
