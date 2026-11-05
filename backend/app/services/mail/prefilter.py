"""Pre-analysis filters applied before sending an email to the LLM.

Two responsibilities:
  1. Block senders/domains that are clearly never job-related (saves CPU time
     and avoids false positives from marketing, finance, food-delivery emails).
  2. Strip common boilerplate from email bodies so the LLM sees cleaner text.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Sender domain blocklist
# Emails from these domains are skipped without calling the LLM.
# ---------------------------------------------------------------------------
_BLOCKED_DOMAINS: frozenset[str] = frozenset(
    {
        # Finance / banking
        "bankofamerica.com", "ealerts.bankofamerica.com",
        "chase.com", "chaseonline.chase.com",
        "wellsfargo.com", "wellsfargoemail.com",
        "citi.com", "citibank.com",
        "americanexpress.com",
        "capitalone.com",
        "discover.com",
        "fidelity.com", "fidelityinvestments.com",
        "vanguard.com",
        "schwab.com",
        # Payroll / HR operations
        "paycomonline.com",
        "adp.com",
        "paychex.com",
        "gusto.com",
        "rippling.com",
        # Food delivery / restaurants
        "doordash.com",
        "ubereats.com",
        "grubhub.com",
        "seamless.com",
        "olo.com",           # online ordering platform used by many restaurants
        "daveshotchicken.com",
        "chipotle.com",
        "dominos.com",
        # Ride-sharing / transport
        "uber.com", "ridereceipts.uber.com",
        "lyft.com",
        # E-commerce / retail
        "amazon.com", "ship.amazon.com", "shipment-tracking.amazon.com",
        "ebay.com",
        "etsy.com",
        "shopify.com",
        "bestbuy.com",
        "target.com",
        "walmart.com",
        # Payments / fintech
        "paypal.com",
        "venmo.com",
        "stripe.com",
        "square.com",
        "cashapp.com",
        "zelle.com",
        # Streaming / subscriptions
        "netflix.com",
        "spotify.com",
        "hulu.com",
        "disneyplus.com",
        "apple.com", "appleid.apple.com",
        "google.com", "accounts.google.com",
        "microsoft.com", "account.microsoft.com",
        # Social / comms
        "twitter.com", "x.com",
        "facebook.com", "facebookmail.com",
        "instagram.com",
        "linkedin.com",      # LinkedIn *job* emails come through workday/greenhouse etc.
        "tiktok.com",
        # Misc marketing / notifications
        "mailchimp.com",
        "constantcontact.com",
        "sendgrid.net",
        "klaviyo.com",
        "twilio.com",
    }
)

# Known job-platform senders that should always pass through.
_JOB_PLATFORM_DOMAINS: frozenset[str] = frozenset(
    {
        "myworkday.com", "otp.workday.com",
        "greenhouse.io",
        "lever.co",
        "icims.com",
        "taleo.net",
        "jobvite.com",
        "smartrecruiters.com",
        "bamboohr.com",
        "ashbyhq.com",
        "ripplematching.com",
        "wellfound.com",
    }
)

# Sender display-name keywords that almost certainly indicate non-job mail.
_BLOCKED_NAME_KEYWORDS: tuple[str, ...] = (
    "no-reply@olo",
    "doordash",
    "ubereats",
    "grubhub",
    "payroll",
    "timesheet",
    "bank of america",
    "wells fargo",
    "paypal",
    "venmo",
    "your order",
    "your receipt",
    "your delivery",
    "your shipment",
    "tracking number",
)

# ---------------------------------------------------------------------------
# Body boilerplate patterns to strip before analysis
# ---------------------------------------------------------------------------
_BOILERPLATE_PATTERNS: list[re.Pattern[str]] = [
    # Unsubscribe / manage preferences lines
    re.compile(
        r"(unsubscribe|manage (your )?preferences?|opt.?out|email preferences?)"
        r".{0,200}",
        re.IGNORECASE,
    ),
    # Legal / privacy footers
    re.compile(
        r"(this (email|message) (was sent|is intended)|confidentiality notice"
        r"|if you (received|believe) (this|you received)|privacy policy"
        r"|terms (of service|and conditions)|©\s*\d{4})"
        r".{0,400}",
        re.IGNORECASE | re.DOTALL,
    ),
    # View in browser / having trouble links
    re.compile(
        r"(view (this )?(email|message) (in|online|on)|having trouble viewing|"
        r"click here to view).{0,120}",
        re.IGNORECASE,
    ),
    # Physical address lines (common at the bottom of marketing emails)
    re.compile(r"\d{1,5}\s+\w[\w\s]+,\s+\w[\w\s]+,\s+[A-Z]{2}\s+\d{5}", re.IGNORECASE),
    # Excessive whitespace / blank lines left by stripping
    re.compile(r"\n{3,}"),
]


def _extract_domain(sender: str) -> str:
    """Return the lowercase domain from a From header."""
    # "Appian <careers@appian.com>" → "appian.com"
    m = re.search(r"@([\w.\-]+)", sender)
    return m.group(1).lower() if m else ""


def is_blocked(sender: str) -> bool:
    """Return True if this sender should be skipped without LLM analysis."""
    domain = _extract_domain(sender)

    # Job platforms always pass through regardless of other rules.
    for jp in _JOB_PLATFORM_DOMAINS:
        if domain == jp or domain.endswith("." + jp):
            return False

    # Block by domain.
    if domain in _BLOCKED_DOMAINS:
        return True
    for blocked in _BLOCKED_DOMAINS:
        if domain.endswith("." + blocked):
            return True

    # Block by display-name / full sender string keywords.
    lower = sender.lower()
    for kw in _BLOCKED_NAME_KEYWORDS:
        if kw in lower:
            return True

    return False


# OTP / verification emails are job-adjacent but carry no useful tracking data.
_OTP_SUBJECT_RE = re.compile(
    r"(security code|verification code|verify your (email|account)|"
    r"one[- ]time (code|password)|confirm your identity|"
    r"final step to complete|action required.*verif|"
    r"enter (this|the) code|otp\b)",
    re.IGNORECASE,
)
_OTP_BODY_RE = re.compile(
    r"(security code|verification code|one[- ]time (code|password)|"
    r"confirm your identity|copy and paste this code|"
    r"use this code to confirm|code will expire|"
    r"enter the code|resubmit your application)",
    re.IGNORECASE,
)


def is_otp_or_verification(subject: str, body: str) -> bool:
    """True for OTP / email-verify messages that should not create applications."""
    if _OTP_SUBJECT_RE.search(subject or ""):
        return True
    # Body-only match: require a short numeric/alphanumeric code pattern nearby
    # so we don't drop real "thank you for applying" emails that mention verify.
    snippet = (body or "")[:800]
    if _OTP_BODY_RE.search(snippet) and re.search(
        r"\b[A-Za-z0-9]{4,10}\b", snippet
    ):
        # Prefer subject signals; for body, also check it's not a rich confirmation.
        if not re.search(
            r"(thank you for apply|we received your application|"
            r"application for the .+ role)",
            snippet,
            re.IGNORECASE,
        ):
            return True
    return False


def clean_body(body: str, max_chars: int = 4000) -> str:
    """Strip boilerplate from an email body and truncate."""
    text = body
    for pattern in _BOILERPLATE_PATTERNS:
        if pattern.pattern == r"\n{3,}":
            text = pattern.sub("\n\n", text)
        else:
            text = pattern.sub(" ", text)
    # Collapse leftover whitespace noise
    text = re.sub(r"[ \t]{3,}", "  ", text)
    return text.strip()[:max_chars]
