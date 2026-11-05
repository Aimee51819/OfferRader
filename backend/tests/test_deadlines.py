from datetime import datetime, timezone

import pytest

from app.domain.deadlines import add_business_days, localize, normalize_deadline, resolve_timezone

# Fri 2027-03-12 10:30 EST. US DST starts Sun 2027-03-14 02:00.
RECV = datetime(2027, 3, 12, 15, 30, tzinfo=timezone.utc)


def n(text, **kw):
    return normalize_deadline(text, received_at=kw.pop("received_at", RECV), **kw)


def test_hours_are_elapsed_time_across_dst():
    r = n("within 72 hours of receiving this email")
    assert r.due_at == datetime(2027, 3, 15, 15, 30, tzinfo=timezone.utc)
    assert r.precision == "relative" and r.anchor == "email_received_at" and not r.needs_review


def test_days_cross_dst_disclose_assumption():
    r = n("Complete within 3 days of receipt")
    assert r.due_at == datetime(2027, 3, 15, 14, 30, tzinfo=timezone.utc)
    assert any("DST" in a for a in r.assumptions)


def test_sent_anchor_used_when_wording_says_sent():
    sent = datetime(2027, 3, 12, 12, 0, tzinfo=timezone.utc)
    r = n("within 48 hours of when this email was sent", sent_at=sent)
    assert r.anchor == "email_sent_at" and r.due_at == datetime(2027, 3, 14, 12, 0, tzinfo=timezone.utc)


def test_time_limit_after_opening_is_not_a_deadline():
    r = n("You have 90 minutes to finish once you begin the test")
    assert r.due_at is None and r.needs_review


def test_date_only_never_invents_time():
    r = n("by March 20, 2027")
    assert r.precision == "day" and r.due_date == "2027-03-20" and r.due_at is None
    assert not r.needs_review


def test_time_without_timezone_has_no_instant():
    r = n("by 5:00 PM on March 18, 2027")
    assert r.due_at is None and r.due_date == "2027-03-18" and r.needs_review


@pytest.mark.parametrize(
    "text,expected",
    [
        ("by March 20, 2027 at 11:59 PM ET", datetime(2027, 3, 21, 3, 59, tzinfo=timezone.utc)),
        ("no later than 5:00 PM PT on 3/16/2027", datetime(2027, 3, 17, 0, 0, tzinfo=timezone.utc)),
        ("Deadline: 2027-03-19 17:00 UTC", datetime(2027, 3, 19, 17, 0, tzinfo=timezone.utc)),
        ("by March 19, 2027 09:00 +05:30", datetime(2027, 3, 19, 3, 30, tzinfo=timezone.utc)),
        ("before Friday, March 19, 2027 at noon ET", datetime(2027, 3, 19, 16, 0, tzinfo=timezone.utc)),
    ],
)
def test_absolute_with_zone(text, expected):
    r = n(text)
    assert r.due_at == expected and r.precision == "instant"


def test_wrong_dst_abbreviation_uses_real_offset_and_says_so():
    r = n("by 5:00 PM EST on March 16, 2027")  # EST written, but EDT in effect
    assert r.due_at == datetime(2027, 3, 16, 21, 0, tzinfo=timezone.utc)
    assert any("daylight" in a for a in r.assumptions)


def test_nonexistent_and_ambiguous_local_times_need_review():
    gap = n("by March 14, 2027 2:30 AM ET")
    overlap = n("by November 7, 2027 1:30 AM ET")
    assert gap.due_at is None and gap.needs_review and "does not exist" in gap.assumptions[-1]
    assert overlap.due_at is None and overlap.needs_review and "occurs twice" in overlap.assumptions[-1]


def test_midnight_is_flagged():
    r = n("by midnight on March 15, 2027 ET")
    assert r.needs_review and r.due_at == datetime(2027, 3, 16, 4, 0, tzinfo=timezone.utc)
    assert any("ambiguous" in a for a in r.assumptions)


def test_business_days_skip_weekends_and_report_holidays():
    d, holidays = add_business_days(datetime(2027, 5, 27).date(), 3)  # Thu before Memorial Day
    assert d.isoformat() == "2027-06-01" and any("Memorial Day" in h for h in holidays)
    r = n("within three business days", received_at=datetime(2027, 5, 27, 16, 0, tzinfo=timezone.utc))
    assert r.needs_review and r.due_at is None and r.due_date == "2027-06-01"


def test_missing_year_is_inferred_and_disclosed():
    r = n("by March 18 at 5pm ET")
    assert r.due_at == datetime(2027, 3, 18, 21, 0, tzinfo=timezone.utc)
    assert any("No year" in a for a in r.assumptions)


def test_garbage_is_unknown_not_guessed():
    r = n("as soon as you can")
    assert r.due_at is None and r.due_date is None and r.needs_review


def test_lowercase_at_is_not_a_timezone():
    tz, _, _ = resolve_timezone(None)
    assert tz is None
    r = n("by March 20, 2027 at 3 pm")
    assert r.due_at is None and r.needs_review  # 'at' must not be read as a zone


def test_localize_roundtrip():
    from zoneinfo import ZoneInfo

    ok, problem = localize(datetime(2027, 7, 1, 12, 0), ZoneInfo("America/New_York"))
    assert problem is None and ok.utcoffset().total_seconds() == -4 * 3600
