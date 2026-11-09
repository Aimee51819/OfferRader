"""Deterministic normalization helpers: company names, role titles, URLs, IDs."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_LEGAL_SUFFIXES = re.compile(
    r"\b(incorporated|inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|plc|gmbh|"
    r"ag|sa|nv|bv|lp|llp|pbc)\b\.?",
    re.IGNORECASE,
)
_NOISE_COMPANY_WORDS = re.compile(
    r"\b(careers?|recruiting|recruitment|talent acquisition|talent|jobs|hiring team|"
    r"university recruiting|campus recruiting|hr|human resources|no-?reply|notifications?)\b",
    re.IGNORECASE,
)


def _ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def normalize_company(name: str | None) -> str:
    if not name:
        return ""
    text = _ascii_fold(name).lower().replace("&", " and ")
    text = re.sub(r"^the\s+", "", text)
    text = _LEGAL_SUFFIXES.sub(" ", text)
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_company_display(name: str) -> str:
    """Strip recruiting-team noise from a sender display name ('IBM Careers' -> 'IBM')."""
    text = _NOISE_COMPANY_WORDS.sub(" ", name)
    text = re.sub(r"\s+", " ", text).strip(" -|,:")
    return text or name.strip()


_TITLE_SYNONYMS = [
    (r"\bswe\b", "software engineer"),
    (r"\bsde\b", "software engineer"),
    (r"\bsoftware development engineer\b", "software engineer"),
    (r"\bsoftware developer\b", "software engineer"),
    (r"\btpm\b", "technical program manager"),
    (r"\bapm\b", "associate product manager"),
    (r"\bpm\b", "product manager"),
    (r"\bse\b", "solutions engineer"),
    (r"\bsa\b", "solutions architect"),
    (r"\binternship\b", "intern"),
    (r"\bco-?op\b", "intern"),
    (r"\bsr\b", "senior"),
    (r"\bjr\b", "junior"),
    (r"\beng\b", "engineer"),
    (r"\bmgr\b", "manager"),
]
_TITLE_NOISE = re.compile(
    r"\b(summer|fall|winter|spring)\b|\b20\d\d\b|\b(us|usa|remote|hybrid|onsite|on-site)\b",
    re.IGNORECASE,
)


def normalize_title(title: str | None) -> str:
    if not title:
        return ""
    text = _ascii_fold(title).lower()
    text = re.sub(r"\(.*?\)", " ", text)  # "(Summer 2027)" etc.
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9 \-]", " ", text)
    for pattern, repl in _TITLE_SYNONYMS:
        text = re.sub(pattern, repl, text)
    text = _TITLE_NOISE.sub(" ", text)
    text = re.sub(r"[\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity of two normalized titles (token Jaccard blended with ratio)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb)
    ratio = SequenceMatcher(None, na, nb).ratio()
    return round(0.6 * jaccard + 0.4 * ratio, 4)


def is_placeholder_title(title: str | None) -> bool:
    return normalize_title(title) in {"", "unknown", "n a", "na", "none", "null", "tbd", "role"}


# Query parameters that identify a posting; everything else (utm_*, source, ...) is dropped.
_IDENTIFYING_PARAMS = {"gh_jid", "jobid", "job_id", "jid", "jk", "req", "reqid", "id", "pid"}


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return None
    path = re.sub(r"/+$", "", parts.path) or ""
    path = re.sub(r"/(apply|application)$", "", path, flags=re.IGNORECASE)
    query = urlencode(
        sorted(
            (k.lower(), v)
            for k, v in parse_qsl(parts.query, keep_blank_values=False)
            if k.lower() in _IDENTIFYING_PARAMS
        )
    )
    return urlunsplit(("https", host, path, query, ""))


def safe_http_url(url: str | None) -> str | None:
    """Return the URL only if it is plain http(s) with no credentials or control chars."""
    if not url or len(url) > 2048:
        return None
    if re.search(r"[\s\x00-\x1f<>\"']", url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username or parts.password:
        return None
    return url


_REQ_PATTERNS = [
    # "Requisition ID: R0123456", "Job ID #12345", "Req. 98765", "Posting ID: JR-1234"
    re.compile(
        r"\b(?:requisition|req|job|posting|position|vacancy|opening)\.?\s*"
        r"(?:id|no|number|#)?\.?\s*[:#]?\s*([A-Z]{0,4}-?\d{3,10}(?:-\d{1,4})?)\b",
        re.IGNORECASE,
    ),
    # Workday style "JR-123456" / "R-0123456" / "REQ-1234"
    re.compile(r"\b((?:JR|REQ|R)-?\d{4,9})\b"),
    # IBM style "Name - 128506 - Software Developer"
    re.compile(r"(?:^|\s[-–—]\s)(\d{5,8})\s*[-–—]\s+[A-Za-z]"),
]


def extract_requisition_ids(*texts: str | None) -> list[str]:
    seen: list[str] = []
    blob = "\n".join(t for t in texts if t)
    for pat in _REQ_PATTERNS:
        for m in pat.finditer(blob):
            value = normalize_req_id(m.group(1))
            if value and value not in seen and not re.fullmatch(r"20\d\d", value):
                seen.append(value)
    return seen


def normalize_req_id(value: str | None) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", "", value).upper().strip("#:.")
    return text or None


_ATS_HOSTS = {
    "greenhouse": ("greenhouse.io", "greenhouse-mail.io"),
    "lever": ("lever.co", "hire.lever.co"),
    "workday": ("myworkday.com", "myworkdayjobs.com", "workday.com"),
    "icims": ("icims.com",),
    "ashby": ("ashbyhq.com",),
    "smartrecruiters": ("smartrecruiters.com",),
    "jobvite": ("jobvite.com",),
    "taleo": ("taleo.net",),
    "successfactors": ("successfactors.com", "successfactors.eu"),
    "eightfold": ("eightfold.ai",),
    "oracle": ("oraclecloud.com",),
    "brassring": ("brassring.com",),
    "handshake": ("joinhandshake.com",),
}
_ASSESSMENT_HOSTS = {
    "HackerRank": ("hackerrank.com", "hackerrankforwork.com"),
    "CodeSignal": ("codesignal.com",),
    "HireVue": ("hirevue.com",),
    "Codility": ("codility.com",),
    "Karat": ("karat.com", "karat.io"),
    "Pymetrics": ("pymetrics.com", "pymetrics.ai"),
    "Coderbyte": ("coderbyte.com",),
    "SHL": ("shl.com",),
    "Mettl": ("mettl.com",),
    "Plum": ("plum.io",),
}


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def domain_of(address_or_url: str | None) -> str | None:
    if not address_or_url:
        return None
    text = address_or_url.strip().lower()
    if "@" in text and "://" not in text:
        return text.rsplit("@", 1)[1].strip(">").strip() or None
    try:
        host = urlsplit(text if "://" in text else "https://" + text).hostname
    except ValueError:
        return None
    return host


def detect_ats(*addresses_or_urls: str | None) -> str | None:
    for item in addresses_or_urls:
        host = domain_of(item)
        if not host:
            continue
        for name, suffixes in _ATS_HOSTS.items():
            if _host_matches(host, suffixes):
                return name
    return None


def detect_assessment_provider(*texts_or_hosts: str | None) -> str | None:
    blob = " ".join(t for t in texts_or_hosts if t).lower()
    for name, suffixes in _ASSESSMENT_HOSTS.items():
        if name.lower() in blob or any(s in blob for s in suffixes):
            return name
    return None


def is_shared_sender_domain(domain: str | None) -> bool:
    """ATS / assessment / mail platforms send for many companies: never a company alias."""
    if not domain:
        return True
    shared = [s for v in _ATS_HOSTS.values() for s in v] + [
        s for v in _ASSESSMENT_HOSTS.values() for s in v
    ]
    shared += [
        "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
        "sendgrid.net", "mailgun.org", "amazonses.com", "linkedin.com", "indeed.com",
        "glassdoor.com", "ziprecruiter.com", "joinhandshake.com", "ripplematching.com",
    ]
    return _host_matches(domain, tuple(shared))


def infer_category(title: str | None) -> str | None:
    """Category from the role title. Order matters: the first matching rule wins.

    * "solution(s)" anywhere -> SE (solutions engineer / architect / consultant)
    * "product" or "production" -> PM
    * "software" -> SDE/SWE
    """
    t = normalize_title(title)
    if not t:
        return "Other" if (title or "").strip() else None
    if "solution" in t:
        return "SE"
    if "product" in t or "production" in t:
        return "PM"
    if "software" in t or "developer" in t or "sde" in t.split() or "swe" in t.split():
        return "SDE/SWE"
    if "data" in t or "analytics" in t or "machine learning" in t:
        return "Data"
    return "Other"
