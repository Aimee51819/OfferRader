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

# ATS / assessment / campus-recruiting senders. Mail from these always reaches the model.
_ATS_SENDER_DOMAINS: frozenset[str] = _JOB_PLATFORM_DOMAINS | frozenset(
    {
        "myworkdayjobs.com", "workday.com", "greenhouse-mail.io", "hire.lever.co",
        "successfactors.com", "successfactors.eu", "oraclecloud.com", "brassring.com",
        "eightfold.ai", "avature.net", "tal.net", "phenompeople.com", "paradox.ai",
        "workablemail.com", "workable.com", "recruitee.com", "teamtailor.com", "pinpointhq.com",
        "jazzhr.com", "applytojob.com", "breezy.hr", "dover.com", "gem.com", "beamery.com",
        "hackerrank.com", "hackerrankforwork.com", "codesignal.com", "hirevue.com",
        "hirevue-app.eu", "modernhire.com", "pymetrics.ai", "harver.com", "shl.com",
        "talentlens.com", "criteriacorp.com", "codility.com", "karat.io", "joinhandshake.com",
        "mokahr.com", "hotjob.cn", "zhiye.com", "beisen.com", "nowcoder.com",
    }
)

# Local-part / subdomain hints that a company's own recruiting team is writing.
_RECRUITING_SENDER_HINT = re.compile(
    r"(?<![a-z])(careers?|recruit\w*|talent\w*|hiring|campus\w*|earlycareers|jobs?|staffing|"
    r"candidates?)(?![a-z])",
    re.IGNORECASE,
)

# Phrases that appear in real application lifecycle mail (EN + ZH). Deliberately specific:
# generic words like "job", "hiring" or "offer" also appear in job-board and retail marketing.
_JOB_LIFECYCLE_RE = re.compile(
    r"(thank(?:s| you) for (?:your )?(?:appl|submitting your application|your interest in)|"
    r"(?:we(?:'ve| have)?) (?:received|got) your application|got your .{0,40}application|"
    r"your application (?:to|for|with|has|was|is|status)|"
    r"application (?:received|submitted|update|status|id|number)|"
    r"candidate (?:home|portal|id|profile)|requisition|"
    r"(?:online|coding|technical|video|digital) (?:assessment|test|interview)|assessment invitation|"
    r"complete (?:the|your|an|this) (?:assessment|test|challenge)|"
    r"interview (?:invitation|invite|request|schedul|availability|confirmation)|"
    r"(?:schedule|book) (?:an?|your) (?:interview|call)|phone screen|recruiter (?:call|screen)|"
    r"next steps? in (?:the|our) (?:hiring|recruiting|recruitment|interview|selection) process|"
    r"unfortunately|regret to inform|not (?:be )?(?:moving|move) forward|"
    r"(?:move|moving) forward with other candidates|decided to (?:pursue|proceed with) other|"
    r"position has been filled|offer (?:letter|of employment)|pleased to (?:offer|extend)|"
    r"you(?:'ve| have) been referred|"
    r"感谢.{0,4}(?:应聘|申请|投递|关注)|简历.{0,4}(?:已|收到|投递|筛选)|"
    r"(?:申请|投递).{0,4}(?:成功|已收到|已提交|进度|状态)|网申|笔试|测评|面试|录用|"
    r"人才库|遗憾地通知|很遗憾|内推)",
    re.IGNORECASE,
)


def is_ats_sender(sender: str) -> bool:
    domain = _extract_domain(sender)
    return any(domain == d or domain.endswith("." + d) for d in _ATS_SENDER_DOMAINS)


def has_job_signal(sender: str, subject: str, body: str) -> str | None:
    """Positive gate: return why this email looks like application mail, or None.

    Only mail with a positive signal is sent to the local model; everything else is
    ignored deterministically (fast, and the small model never sees newsletters).
    """
    if is_ats_sender(sender):
        return "ats_sender"
    head = f"{subject}\n{(body or '')[:4000]}"
    if _JOB_LIFECYCLE_RE.search(head):
        return "lifecycle_phrase"
    m = re.search(r"([\w.+\-]+)@([\w.\-]+)", sender or "")
    if m and _RECRUITING_SENDER_HINT.search(f"{m.group(1)}.{m.group(2)}") and not is_blocked(sender):
        return "recruiting_sender"
    return None


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
