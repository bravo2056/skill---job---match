"""
email-scanner.py - script-orchestrator scanner for the job-search workflow.

Replaces Phase 1 (Enumerate/Extract) of the agent-orchestrated email-scanner-v2.
Per-sender deterministic parsers; no LLM in the extraction path.

Phase 2 (ingest from staging into integrity.py) is a separate script.
This file only writes scan-staging.json and rejection-staging.json.
integrity.py owns every DB-touching gate (Verizon stop, comp ceiling,
commute, PMP, dedup, status assignment).

Hard rules:
  - Filter logic stays in integrity.py. No filtering here.
  - Link extraction is per-sender regex, never LLM.
  - JD content is literal extraction, capped at 600 chars, never paraphrased.
  - No batched extraction. Per-sender parsers iterate per role block.
  - Phase 2 calls integrity.py per row. No subprocess wrappers.
"""
import base64
import json
import os
import quopri
import re

# ---------- Paths ----------
STAGING_PATH = r'C:/Users/Garrison/career/scan-staging.json'
REJECTION_STAGING_PATH = r'C:/Users/Garrison/career/rejection-staging.json'
UNPARSED_STAGING_PATH = r'C:/Users/Garrison/career/unparsed-staging.json'
COMPLETION_SENTINEL = r'C:/Users/Garrison/career/scan-staging.json.complete'

JD_CAP = 600  # max chars for jd_excerpt; literal extraction, no summarization


# =====================================================================
# Per-sender deterministic parsers
#
# Each parser takes (subject, sender, body) -> list of row dicts matching the
# scan-staging.json schema:
#   {source_email, company, role_title, comp, location, remote_status,
#    canonical_link, jd_excerpt, staffing_agency, inferred_employer, notes}
#
# `thread_id` is added post-parse by scan_digest() so parsers don't need it.
#
# Schema bridge: integrity.py accepts these long names via alias
# (source_email→source, role_title→role, canonical_link→link) and silently
# ignores jd_excerpt. So scan-staging.json can be passed straight to
# `integrity.py --action ingest_batch` without a transform step.
#
# Add a new sender by writing a parser function and registering it in PARSERS.
# =====================================================================


# --- donotreply@match.indeed.com (one role per email) ---
# Subject format: "[role_title] @ [company]"
# Plaintext body structure after the intro paragraph:
#   [role_title]
#   [company]
#   [location]
#   Salary: ...                 (optional)
#   Job type: ...               (optional)
#   Schedule: ...               (optional)
#   Work setting: ...           (optional)
#   Benefits:                   (optional, followed by indented "  - " bullets)
#   View job: https://cts.indeed.com/v3/...
#   Apply now: https://cts.indeed.com/v3/...

INDEED_MATCH_VIEW_URL = re.compile(r'View job:\s*(https://cts\.indeed\.com/v3/\S+)')
INDEED_MATCH_SUBJECT_RE = re.compile(r'^(.*?)\s+@\s+(.+?)\s*$')


def parse_indeed_match(subject, sender, body):
    """Parse one role from a donotreply@match.indeed.com email."""

    role_title, company = '', ''
    m = INDEED_MATCH_SUBJECT_RE.match(subject or '')
    if m:
        role_title = m.group(1).strip()
        company = m.group(2).strip()
    else:
        role_title = (subject or '').strip()

    url_match = INDEED_MATCH_VIEW_URL.search(body)
    canonical_link = url_match.group(1) if url_match else ''

    location = _indeed_match_location(body)
    comp = _labelled_field(body, 'Salary')
    remote_status = _remote_status(location, _labelled_field(body, 'Work setting'))
    jd_excerpt = _indeed_match_jd(body)

    return [{
        'source_email': sender,
        'company': company,
        'role_title': role_title,
        'comp': comp,
        'location': location,
        'remote_status': remote_status,
        'canonical_link': canonical_link,
        'jd_excerpt': jd_excerpt[:JD_CAP],
        'staffing_agency': False,
        'inferred_employer': '',
        'notes': '',
    }]


def _indeed_match_location(body):
    """Location is the line directly above the first labelled field
    (Salary, Job type, Schedule, Work setting, or Benefits)."""
    lines = body.splitlines()
    label_pat = re.compile(r'^(Salary|Job type|Schedule|Work setting|Benefits):')
    for i, line in enumerate(lines):
        if label_pat.match(line):
            j = i - 1
            while j > 0 and not lines[j].strip():
                j -= 1
            return lines[j].strip()
    return ''


def _labelled_field(body, label):
    """Single-line labelled field at start of a line, e.g. 'Salary: $X'."""
    pat = re.compile(r'^' + re.escape(label) + r':\s*(.+?)\s*$', re.MULTILINE)
    m = pat.search(body)
    return m.group(1).strip() if m else ''


def _remote_status(location, work_setting):
    haystack = f'{location} {work_setting}'.lower()
    if 'remote' in haystack:
        return 'Remote'
    if 'hybrid' in haystack:
        return 'Hybrid'
    return ''


def _indeed_match_jd(body):
    """match emails put labelled fields and View/Apply URLs in plaintext;
    the actual JD body lives only in HTML. The intro paragraph after
    'Hi Garrison,' is the only plaintext narrative; use it as a literal
    excerpt. No paraphrasing, just whitespace-collapsed text."""
    m = re.search(r'Hi Garrison,\s*\n\s*(.+?)\n\s*\n', body, re.DOTALL)
    if m:
        return ' '.join(m.group(1).split())
    return ''


# --- donotreply@jobalert.indeed.com (multi-role digest) ---
# Each role block in plaintext is six or seven lines:
#   [role_title]
#   [company] - [location]
#   [salary]                  (optional)
#   Easily apply              (optional)
#   [snippet]
#   [posted_date]
#   [URL]
# Blocks are separated by a blank line. The URL line is the END anchor.

INDEED_JOBALERT_URL = re.compile(
    r'https://www\.indeed\.com/(?:rc/clk/dl\?jk=?[a-fA-F0-9]+[^\s]*|pagead/clk/dl\?\S+)'
)
INDEED_SALARY_RE = re.compile(
    r'^(From\s+)?\$[\d,.]+(\s*-\s*\$?[\d,.]+)?\s+(a year|a week|an hour|a month|a day)$'
)


def parse_indeed_jobalert(subject, sender, body):
    """Parse all roles from a donotreply@jobalert.indeed.com digest."""
    lines = body.splitlines()

    rows = []
    for i, line in enumerate(lines):
        if not INDEED_JOBALERT_URL.fullmatch(line.strip()):
            continue

        # Walk backward to start of block (the line after the previous blank line).
        start = i
        while start > 0 and lines[start - 1].strip():
            start -= 1

        block = [l.strip() for l in lines[start:i + 1] if l.strip()]
        if len(block) < 5:
            continue  # malformed

        role_title = block[0]
        company_location = block[1]
        snippet = block[-3]
        url = block[-1]
        # block[-2] is the posted_date - informational, not staged.

        comp = ''
        for mid in block[2:-3]:
            if INDEED_SALARY_RE.match(mid):
                comp = mid
                break

        if ' - ' in company_location:
            company, location = company_location.split(' - ', 1)
        else:
            company, location = company_location, ''

        rows.append({
            'source_email': sender,
            'company': company.strip(),
            'role_title': role_title,
            'comp': comp,
            'location': location.strip(),
            'remote_status': _remote_status(location, ''),
            'canonical_link': url,
            'jd_excerpt': snippet[:JD_CAP],
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': '',
        })

    return rows


# --- ali@hiring.cafe (multi-role digest; HTML in the text/plain MIME part) ---
# HiringCafe places HTML markup inside the text/plain part. Role cards live
# inside a <td> with a fixed style; each card has:
#   <h3 style="margin:0 0 6px 0;font-size:1.06em;line-height:1.25;">
#     <span style="color:#18181a;font-weight:600;">[role_title]</span>
#   </h3>
#   <div style="color:#18181a;margin-bottom:2px;font-weight:500;">
#     [company] — [location] ([work_type])
#   </div>
#   <div style="color:#75767b;font-size:.92em;margin-bottom:8px;">[posted_date]</div>
#   <span style="display:inline-block;background:#d1fae5;...">[salary]</span>
#   <div style="color:#58595d;font-size:.94em;line-height:1.4;">[description]</div>
#   ...
#   <a href="https://u52508838.ct.sendgrid.net/ls/click?...">Apply</a>
#
# Same job often appears under multiple saved-search sections with different
# sendgrid tracking URLs each time. Per-row dedup is integrity.py's job.

HIRING_CAFE_CARD_ANCHOR = re.compile(
    r'<h3\s+style="margin:0 0 6px 0;font-size:1\.06em;line-height:1\.25;">'
)
HIRING_CAFE_TITLE = re.compile(
    r'<span\s+style="color:#18181a;font-weight:600;">([^<]+)</span>'
)
HIRING_CAFE_COMPANY_LOC = re.compile(
    r'<div\s+style="color:#18181a;margin-bottom:2px;font-weight:500;">\s*(.+?)\s*</div>',
    re.DOTALL,
)
HIRING_CAFE_SALARY = re.compile(
    r'<span\s+style="display:inline-block;background:#d1fae5;[^"]*">\s*(.+?)\s*</span>',
    re.DOTALL,
)
HIRING_CAFE_DESC = re.compile(
    r'<div\s+style="color:#58595d;font-size:\.94em;line-height:1\.4;">\s*(.+?)\s*</div>',
    re.DOTALL,
)
HIRING_CAFE_APPLY = re.compile(
    r'<a\s+href="(https://u52508838\.ct\.sendgrid\.net/ls/click\?[^"]+)"[^>]*>\s*Apply\s*</a>',
    re.DOTALL,
)
HIRING_CAFE_LOC_WORKTYPE = re.compile(r'^(.*?)\s*\(([^)]+)\)\s*$')


def parse_hiring_cafe(subject, sender, body):
    """Parse all role cards from an ali@hiring.cafe digest."""

    starts = [m.start() for m in HIRING_CAFE_CARD_ANCHOR.finditer(body)]
    if not starts:
        return []
    starts.append(len(body))  # sentinel for last chunk

    rows = []
    for i in range(len(starts) - 1):
        chunk = body[starts[i]:starts[i + 1]]

        title_m = HIRING_CAFE_TITLE.search(chunk)
        company_loc_m = HIRING_CAFE_COMPANY_LOC.search(chunk)
        apply_m = HIRING_CAFE_APPLY.search(chunk)
        if not (title_m and company_loc_m and apply_m):
            continue

        role_title = re.sub(r'\s+', ' ', title_m.group(1).strip())
        company_loc = re.sub(r'\s+', ' ', company_loc_m.group(1).strip())
        url = apply_m.group(1).strip()

        # Split "company — location" on em-dash; fall back to plain hyphen.
        if ' — ' in company_loc:
            company, location_field = company_loc.split(' — ', 1)
        elif ' - ' in company_loc:
            company, location_field = company_loc.split(' - ', 1)
        else:
            company, location_field = company_loc, ''

        # Pull "(work_type)" off the end of location.
        loc_m = HIRING_CAFE_LOC_WORKTYPE.match(location_field)
        if loc_m:
            location = loc_m.group(1).strip()
            work_type = loc_m.group(2).strip()
        else:
            location = location_field.strip()
            work_type = ''

        salary_m = HIRING_CAFE_SALARY.search(chunk)
        comp = re.sub(r'\s+', ' ', salary_m.group(1).strip()) if salary_m else ''

        desc_m = HIRING_CAFE_DESC.search(chunk)
        snippet = re.sub(r'\s+', ' ', desc_m.group(1).strip()) if desc_m else ''

        wt = work_type.lower()
        if 'remote' in wt:
            remote_status = 'Remote'
        elif 'hybrid' in wt:
            remote_status = 'Hybrid'
        elif 'onsite' in wt:
            remote_status = 'Onsite'
        else:
            remote_status = ''

        rows.append({
            'source_email': sender,
            'company': company.strip(),
            'role_title': role_title,
            'comp': comp,
            'location': location,
            'remote_status': remote_status,
            'canonical_link': url,
            'jd_excerpt': snippet[:JD_CAP],
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': '',
        })

    return rows


# --- alerts@ziprecruiter.com (multi-role digest; QP-decoded text/plain) ---
# Each role block in the plaintext part:
#   [space][title]  <URL>[New?]
#   [blank]
#   Company • Location[ • In-person|Hybrid|Remote]
#   [optional $salary line]
#   [optional 'Estimated Pay' marker]
#   [space]View Details|1-Click Apply|Apply Now  <URL>
#
# Title-line URL == button-line URL. Title-line shape collides with the CTA
# buttons (View Details / 1-Click Apply / Apply Now / View More Jobs); filter
# those by text. URL path is the stable job ID; query params are tracking.

ZIPRECRUITER_TITLE_RE = re.compile(
    r'^\s+(.+?)\s+<(https://www\.ziprecruiter\.com/(?:ekm|km)/[^>]+)>\s*(?:New)?\s*$'
)
ZIPRECRUITER_BUTTON_TEXTS = {
    'View Details', '1-Click Apply', 'Apply Now', 'View More Jobs',
}


def parse_ziprecruiter(subject, sender, body):
    """Parse all roles from an alerts@ziprecruiter.com digest."""
    lines = body.splitlines()
    rows = []
    i = 0
    while i < len(lines):
        m = ZIPRECRUITER_TITLE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        title_text = m.group(1).strip()
        if title_text in ZIPRECRUITER_BUTTON_TEXTS:
            i += 1
            continue
        canonical_link = m.group(2).split('?')[0].strip()

        # Skip blank lines after title
        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1

        # Company • Location[ • work_type]
        company = location = remote_status = ''
        if j < len(lines) and ' • ' in lines[j]:
            parts = [p.strip() for p in lines[j].split(' • ')]
            company = parts[0] if parts else ''
            location = parts[1] if len(parts) > 1 else ''
            remote_status = parts[2] if len(parts) > 2 else ''
            j += 1

        # Walk a few lines for optional salary + estimated-pay marker
        comp = ''
        is_estimated = False
        scan_end = min(j + 6, len(lines))
        while j < scan_end:
            line = lines[j].strip()
            if not line:
                j += 1
                continue
            if ZIPRECRUITER_TITLE_RE.match(lines[j]):
                break  # next role block
            if line.startswith('$') and len(line) < 60:
                comp = line
            elif line == 'Estimated Pay':
                is_estimated = True
            else:
                break
            j += 1

        rows.append({
            'source_email': sender,
            'company': company,
            'role_title': title_text,
            'comp': comp,
            'location': location,
            'remote_status': remote_status,
            'canonical_link': canonical_link,
            'jd_excerpt': '',
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': 'estimated_pay' if is_estimated else '',
        })

        i = max(j, i + 1)

    return rows


# --- noreply@jobright.ai (HTML-only digest and instant-alert) ---
# Each role card is wrapped in:
#   <a href="https://jobright.ai/jobs/info/[24-hex-id]?utm_source=...">
#     <table ... id="job-section" ...>...card content...</table>
#   </a>
# Inner anchors (title link, apply button, icon link) point at the same URL
# but don't have the table follow-on, so the regex distinguishes outer
# wrappers from inner ones.
#
# Inside the card:
#   <p id="job-title">...<a>title</a></p>
#   <p id="job-company-name">company</p>
#   <p id="job-tag">tag</p>  (×1-3; mixed bag of comp/location/work-type/referrals)
#   <p id="job-match-percentage"><span>NN%</span></p>  (jobright-specific)
#
# Tags classified by content: starts with $ -> comp; Remote/Hybrid/Onsite ->
# remote_status; "N+ referrals" discarded; everything else -> location.

JOBRIGHT_CARD_LINK = re.compile(
    r'<a\s+href="(https://jobright\.ai/jobs/info/[0-9a-f]{24}[^"]*)"[^>]*>\s*<table[^>]*id="job-section"',
    re.DOTALL,
)
JOBRIGHT_TITLE = re.compile(
    r'<p\s+id="job-title"[^>]*>\s*<a[^>]*>\s*(.+?)\s*</a>\s*</p>',
    re.DOTALL,
)
JOBRIGHT_COMPANY = re.compile(
    r'<p\s+id="job-company-name"[^>]*>\s*(.+?)\s*</p>',
    re.DOTALL,
)
JOBRIGHT_TAG = re.compile(
    r'<p\s+id="job-tag"[^>]*>\s*(.+?)\s*</p>',
    re.DOTALL,
)
JOBRIGHT_MATCH = re.compile(
    r'<p\s+id="job-match-percentage"[^>]*>\s*<span>\s*(\d+)\s*(?:<!--[^>]*-->)?\s*%\s*</span>',
    re.DOTALL,
)
JOBRIGHT_REFERRALS_RE = re.compile(r'^\d+\+\s+referrals?$', re.IGNORECASE)
JOBRIGHT_REMOTE_TAGS = {'Remote', 'Hybrid', 'Onsite', 'On-site', 'On site'}


def parse_jobright(subject, sender, body):
    """Parse all role cards from a noreply@jobright.ai email."""
    rows = []
    starts = list(JOBRIGHT_CARD_LINK.finditer(body))
    for idx, m in enumerate(starts):
        chunk_start = m.start()
        chunk_end = starts[idx + 1].start() if idx + 1 < len(starts) else len(body)
        chunk = body[chunk_start:chunk_end]

        canonical_link = m.group(1).split('?')[0].strip()

        title_m = JOBRIGHT_TITLE.search(chunk)
        role_title = _jobright_clean(title_m.group(1)) if title_m else ''

        company_m = JOBRIGHT_COMPANY.search(chunk)
        company = _jobright_clean(company_m.group(1)) if company_m else ''

        comp = location = remote_status = ''
        for tag_m in JOBRIGHT_TAG.finditer(chunk):
            tag = _jobright_clean(tag_m.group(1))
            if not tag or JOBRIGHT_REFERRALS_RE.match(tag):
                continue
            if tag.startswith('$'):
                comp = tag
            elif tag in JOBRIGHT_REMOTE_TAGS:
                remote_status = tag
            else:
                location = tag

        match_m = JOBRIGHT_MATCH.search(chunk)
        notes = f'jobright_match={match_m.group(1)}%' if match_m else ''

        rows.append({
            'source_email': sender,
            'company': company,
            'role_title': role_title,
            'comp': comp,
            'location': location,
            'remote_status': remote_status,
            'canonical_link': canonical_link,
            'jd_excerpt': '',
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': notes,
        })

    return rows


def _jobright_clean(s):
    """Strip HTML tags, comments, and entities; collapse whitespace."""
    s = re.sub(r'<!--[^>]*-->', '', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = (s.replace('&amp;', '&').replace('&#x27;', "'")
           .replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' '))
    return re.sub(r'\s+', ' ', s).strip()


# --- jobs-noreply@linkedin.com & jobalerts-noreply@linkedin.com ---
# Both senders share the same plaintext block structure:
#   [role_title]
#   [company]
#   [location]
#   [optional flag lines: 'Easily apply', 'N company alumni', 'N connection(s)',
#    'This company is actively hiring', 'Apply with resume & profile']
#   View job: https://www.linkedin.com/comm/jobs/view/[NUMERIC_ID]/?...
#   ---------------------------------------------------------    (separator)
#
# 'View job:' is the END anchor. The numeric ID in the URL path is the stable
# dedup key; query params are tracking. Header URLs ('Jobs similar to ... at ...'
# in similar-jobs reminders) are NOT 'View job:'-prefixed and so are skipped.

LINKEDIN_VIEW_URL_RE = re.compile(
    r'^View job:\s*(https://www\.linkedin\.com/comm/jobs/view/\d+[^\s]*)\s*$'
)
LINKEDIN_FLAG_LINES = {
    'This company is actively hiring',
    'Apply with resume & profile',
    'Easily apply',
}
LINKEDIN_FLAG_PATTERNS = [
    re.compile(r'^\d+\+?\s+company\s+alumni\s*$', re.IGNORECASE),
    re.compile(r'^\d+\+?\s+connections?\s*$', re.IGNORECASE),
    re.compile(r'^\d+\+?\s+school\s+alumn(i|us|a)?\s*$', re.IGNORECASE),
]
LINKEDIN_BLOCK_SEPARATOR = re.compile(r'^-{20,}\s*$')


def _is_linkedin_flag(line):
    if line in LINKEDIN_FLAG_LINES:
        return True
    return any(p.match(line) for p in LINKEDIN_FLAG_PATTERNS)


def parse_linkedin_jobs(subject, sender, body):
    """Parse all roles from a LinkedIn job-alert email (handles both senders).
    Anchor on 'View job: <URL>' lines; walk backward to title/company/location."""
    lines = body.splitlines()
    rows = []

    for i, line in enumerate(lines):
        m = LINKEDIN_VIEW_URL_RE.match(line.strip())
        if not m:
            continue
        canonical_link = m.group(1).split('?')[0].rstrip('/').strip()

        # Walk backward, collecting [location, company, title] (reverse order).
        collected = []
        j = i - 1
        while j >= 0 and len(collected) < 3:
            content = lines[j].strip()
            if not content:
                j -= 1
                continue
            if _is_linkedin_flag(content):
                j -= 1
                continue
            if LINKEDIN_BLOCK_SEPARATOR.match(content):
                break
            if LINKEDIN_VIEW_URL_RE.match(content):
                break  # previous block's URL - stop
            collected.append(content)
            j -= 1

        if len(collected) < 3:
            continue  # malformed

        location, company, role_title = collected[0], collected[1], collected[2]

        rows.append({
            'source_email': sender,
            'company': company,
            'role_title': role_title,
            'comp': '',
            'location': location,
            'remote_status': _remote_status(location, ''),
            'canonical_link': canonical_link,
            'jd_excerpt': '',
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': '',
        })

    return rows


# --- dice@connect.dice.com (multi-role digest, plaintext) ---
# Per-role block:
#    <https://www.dice.com/company/[ID]>           (logo link, skipped)
#    [blank]
#    [role_title]                                  (line above the URL anchor)
#    <https://www.dice.com/job-detail/[UUID]>      (anchor)
#    [blank]
#    [company]
#    [blank]
#    [location]                                    (e.g. 'Remote or City, State, USA')
#    [blank]
#    Posted: MM-DD-YYYY                            (informational, not staged)
#
# UUID in the URL path is the stable dedup key.

DICE_JOB_URL_RE = re.compile(
    r'^<(https://www\.dice\.com/job-detail/[a-f0-9-]+)>\s*$'
)


def parse_dice(subject, sender, body):
    """Parse all roles from a dice@connect.dice.com email."""
    lines = body.splitlines()
    rows = []

    for i, line in enumerate(lines):
        m = DICE_JOB_URL_RE.match(line.strip())
        if not m:
            continue
        canonical_link = m.group(1).strip()

        # Title is the first non-blank line directly above the URL anchor.
        role_title = ''
        j = i - 1
        while j >= 0:
            content = lines[j].strip()
            if content:
                role_title = content
                break
            j -= 1

        # Walk forward: skip blanks, take company, skip blanks, take location.
        company = ''
        location = ''
        j = i + 1
        while j < len(lines):
            content = lines[j].strip()
            if not content:
                j += 1
                continue
            if content.startswith('Posted:') or content.startswith('<'):
                break  # block footer or next role
            if not company:
                company = content
            elif not location:
                location = content
                break
            j += 1

        if not role_title or not company:
            continue

        rows.append({
            'source_email': sender,
            'company': company,
            'role_title': role_title,
            'comp': '',
            'location': location,
            'remote_status': _remote_status(location, ''),
            'canonical_link': canonical_link,
            'jd_excerpt': '',
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': '',
        })

    return rows


# --- support@builtin.com (multi-role digest, HTML-only base64) ---
# Built In daily job-match digests are HTML-only with Content-Transfer-Encoding:
# base64. Gmail decodes the CTE before returning, so the body reaches the parser
# as clean HTML via get_plaintext_body's html_only_fallback branch.
#
# Multiple roles can be packed under one <tr id=jobN> wrapper — each role is
# an <a href="https://<sub>.r.<region>.awstrack.me/L0/<urlencoded-builtin-url>...">
# block. Inside each <a>:
#   <div style=margin-bottom:8px;font-size:16px>Company</div>
#   <div style=margin-bottom:8px;font-size:20px;font-weight:700>Role Title</div>
#   <span style=vertical-align:middle>Remote|Hybrid|...</span>   (work mode)
#   <span style=vertical-align:middle>USA|United States|...</span> (location)
#   <span style=vertical-align:middle>$<low>-$<high></span>      (salary, optional)
#
# URL anchor requires %2Fbuiltin.com%2Fjob%2F (singular slug-path) to exclude
# the homepage, profile, and "Get More Recommendations" /jobs CTA links.

BUILTIN_ROLE_BLOCK = re.compile(
    r'<a\s+href="https://[a-z0-9]+\.r\.us-[a-z0-9-]+\.awstrack\.me/L0/'
    r'(https?:%2F%2Fbuiltin\.com%2Fjob%2F[^"]+)"[^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
BUILTIN_TITLE = re.compile(r'font-size:20px;font-weight:700>\s*([^<]+?)\s*</div>')
BUILTIN_COMPANY = re.compile(r'margin-bottom:8px;font-size:16px>\s*([^<]+?)\s*</div>')
BUILTIN_TAG = re.compile(r'<span\s+style=vertical-align:middle>\s*([^<]+?)\s*</span>')


def parse_builtin(subject, sender, body):
    """Parse role rows from a support@builtin.com daily job-match digest."""
    import urllib.parse
    import html as _html
    rows = []
    seen = set()
    for m in BUILTIN_ROLE_BLOCK.finditer(body):
        canonical = urllib.parse.unquote(m.group(1)).split('?', 1)[0]
        if canonical in seen:
            continue
        seen.add(canonical)
        block = m.group(2)
        m_title = BUILTIN_TITLE.search(block)
        m_comp = BUILTIN_COMPANY.search(block)
        if not (m_title and m_comp):
            continue
        tags = [t.strip() for t in BUILTIN_TAG.findall(block)]
        salary = next((t for t in tags if t.startswith('$')), '')
        non_salary = [t for t in tags if not t.startswith('$')]
        work_mode = non_salary[0] if len(non_salary) >= 1 else ''
        location = non_salary[1] if len(non_salary) >= 2 else ''
        rows.append({
            'source_email': sender,
            'company': _html.unescape(m_comp.group(1)).strip(),
            'role_title': _html.unescape(m_title.group(1)).strip(),
            'comp': salary,
            'location': location,
            'remote_status': _remote_status(location, work_mode),
            'canonical_link': canonical,
            'jd_excerpt': '',
            'staffing_agency': False,
            'inferred_employer': '',
            'notes': '',
        })
    return rows


# --- Sender -> parser dispatch ---
PARSERS = {
    'donotreply@match.indeed.com': parse_indeed_match,
    'donotreply@jobalert.indeed.com': parse_indeed_jobalert,
    'ali@hiring.cafe': parse_hiring_cafe,
    'alerts@ziprecruiter.com': parse_ziprecruiter,
    'phil@ziprecruiter.com': parse_ziprecruiter,
    'noreply@jobright.ai': parse_jobright,
    'jobs-noreply@linkedin.com': parse_linkedin_jobs,
    'jobalerts-noreply@linkedin.com': parse_linkedin_jobs,
    'dice@connect.dice.com': parse_dice,
    'support@builtin.com': parse_builtin,
}


# =====================================================================
# Rejection routing - unchanged from spec; rejection extraction is a
# separate concern handled in a follow-up pass.
# =====================================================================
REJECTION_SUBJECT_PHRASES = [
    'regarding your application', 'your application to', 'your application status',
    'update on your application', 'we have decided', 'moved forward with other candidates',
    'not moving forward', 'no longer being considered', 'unable to offer',
    'decided to pursue other', 'position has been filled', 'we will not be moving forward',
]


def route_thread(subject, sender):
    """Return 'digest', 'rejection', or 'skip'.

    Rejection routing is subject-only. An earlier version also routed any
    ATS-domain sender (greenhouse, workday, lever, etc.) to 'rejection',
    which misclassified application confirmations and interview invites
    (same domains, different intent — see curriculumassociates@myworkday.com
    "Your application for ... at ..." → confirmation, not rejection).

    The 12-phrase REJECTION_SUBJECT_PHRASES list is specific enough that
    domain gating adds no recall and removed a false-positive class."""
    subj_lower = (subject or '').lower()
    if any(p in subj_lower for p in REJECTION_SUBJECT_PHRASES):
        return 'rejection'
    if sender in PARSERS:
        return 'digest'
    return 'skip'


# =====================================================================
# Body extraction
# =====================================================================
def get_plaintext_body(gmail, thread_id, event_log):
    """Walk multipart payload. Prefer text/plain (QP-decoded);
    fall back to raw QP-decoded HTML for HTML-only senders (e.g. jobright).
    The raw HTML is returned (not stripped) so per-sender parsers can anchor
    on tag/attribute structure."""
    thread = gmail.users().threads().get(userId='me', id=thread_id, format='full').execute()
    plain, html = [], []
    for msg in thread.get('messages', []):
        _walk_parts(msg.get('payload', {}), plain, html)
    if plain:
        return qp_decode('\n'.join(plain))
    if html:
        event_log('html_only_fallback', f'thread_id={thread_id}', 'pass')
        return qp_decode('\n'.join(html))
    event_log('empty_body', f'thread_id={thread_id}', 'fail')
    return ''


def _walk_parts(part, plain_out, html_out):
    mime = part.get('mimeType', '')
    body = part.get('body', {})
    data = body.get('data')
    if data:
        decoded = base64.urlsafe_b64decode(data).decode('utf-8', errors='replace')
        if mime == 'text/plain':
            plain_out.append(decoded)
        elif mime == 'text/html':
            html_out.append(decoded)
    for sub in part.get('parts', []) or []:
        _walk_parts(sub, plain_out, html_out)


def _html_to_text(html):
    text = re.sub(r'<script\b[^>]*>.*?</script>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style\b[^>]*>.*?</style>', ' ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text).strip()


def qp_decode(body):
    """Quoted-printable decode. Indeed plaintext bodies use QP; '=\\n' is a
    soft line break, '=E2=80=A6' is the ellipsis character, etc.
    Idempotent: bodies that aren't QP-encoded pass through unchanged."""
    if not isinstance(body, str):
        return body
    try:
        return quopri.decodestring(body.encode('utf-8', errors='replace')).decode('utf-8', errors='replace')
    except Exception:
        return body


# =====================================================================
# Staging writes
# =====================================================================
def append_to_staging(path, row):
    """Append one row to staging JSON array. Atomic via temp + replace."""
    rows = []
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                rows = json.load(f)
            if not isinstance(rows, list):
                rows = []
        except (json.JSONDecodeError, OSError):
            rows = []
    rows.append(row)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(rows, f, indent=2)
    os.replace(tmp, path)


# =====================================================================
# Per-thread processing
# =====================================================================
def scan_digest(gmail, thread_id, subject, sender, event_log):
    """Dispatch to per-sender parser. Writes rows to STAGING_PATH."""
    parser = PARSERS.get(sender)
    if parser is None:
        event_log('unconfigured_sender', f'thread_id={thread_id} sender={sender}', 'fail')
        return (0, 0)

    body = get_plaintext_body(gmail, thread_id, event_log)
    if not body:
        return (0, 0)

    try:
        rows = parser(subject, sender, body)
    except Exception as e:
        event_log('parser_failure',
                  f'thread_id={thread_id} sender={sender} err={type(e).__name__}: {str(e)[:100]}',
                  'fail')
        return (0, 0)

    written, skipped = 0, 0
    seen_links = set()
    for row in rows:
        link = row.get('canonical_link', '')
        if link and link in seen_links:
            skipped += 1
            continue
        if link:
            seen_links.add(link)
        # Stamp thread_id post-parse so the agent can group ingest results by
        # thread for selective relabeling (per email-scanner-v2.md Phase 2 Step 4).
        row['thread_id'] = thread_id
        append_to_staging(STAGING_PATH, row)
        written += 1

    event_log('digest_processed',
              f'thread_id={thread_id} sender={sender} written={written} skipped={skipped}',
              'pass')
    return (written, skipped)


def scan_rejection(gmail, thread_id, subject, sender, msg_date, event_log):
    """Stage one rejection record. Per-sender rejection extraction is a
    follow-up; for now stage the routing fields and let Phase 2 fill in."""
    record = {
        'thread_id': thread_id,
        'sender': sender,
        'subject': subject,
        'received_date': msg_date,
        'company': None,
        'role_title': None,
        'resolved_id': None,
        'prior_status': None,
        'match_type': None,
    }
    append_to_staging(REJECTION_STAGING_PATH, record)
    event_log('rejection_staged', f'thread_id={thread_id} sender={sender}', 'pass')
    return True


def scan_unparsed(thread_id, subject, sender, msg_date, event_log):
    """Stage a record for any thread that didn't match a configured parser
    or rejection signal. Surfaces unconfigured senders so the user can see
    what's hitting the inbox and decide whether a parser is worth writing.
    No extraction is attempted - just routing fields."""
    record = {
        'thread_id': thread_id,
        'sender': sender,
        'subject': subject,
        'received_date': msg_date,
    }
    append_to_staging(UNPARSED_STAGING_PATH, record)
    event_log('unparsed_staged', f'thread_id={thread_id} sender={sender}', 'pass')
    return True


# =====================================================================
# Top-level orchestrator
# =====================================================================
def scan_inbox(gmail, threads, event_log):
    """threads: iterable of dicts {thread_id, subject, sender, msg_date}."""
    threads_list = list(threads)
    for p in (STAGING_PATH, REJECTION_STAGING_PATH, UNPARSED_STAGING_PATH):
        if os.path.exists(p):
            os.remove(p)
    if os.path.exists(COMPLETION_SENTINEL):
        os.remove(COMPLETION_SENTINEL)

    # Invariant: if scan-staging.json.complete exists at end, all three
    # canonical staging files MUST also exist with valid JSON. Initialize
    # them as empty arrays here so empty buckets become [] instead of
    # missing files. append_to_staging() handles append-to-existing already.
    for p in (STAGING_PATH, REJECTION_STAGING_PATH, UNPARSED_STAGING_PATH):
        with open(p, 'w', encoding='utf-8') as f:
            json.dump([], f)

    event_log('scan_start', f'threads={len(threads_list)}', 'pass')
    digest_count = rejection_count = skip_count = 0
    for t in threads_list:
        route = route_thread(t['subject'], t['sender'])
        if route == 'digest':
            scan_digest(gmail, t['thread_id'], t['subject'], t['sender'], event_log)
            digest_count += 1
        elif route == 'rejection':
            scan_rejection(gmail, t['thread_id'], t['subject'], t['sender'], t['msg_date'], event_log)
            rejection_count += 1
        else:
            scan_unparsed(t['thread_id'], t['subject'], t['sender'], t['msg_date'], event_log)
            skip_count += 1

    with open(COMPLETION_SENTINEL, 'w'):
        pass
    event_log('scan_complete',
              f'digests={digest_count} rejections={rejection_count} skipped={skip_count}',
              'pass')


# =====================================================================
# Smoke tests — `python email-scanner.py` runs these.
# Library is normally imported, not executed; tests only run on direct invocation.
# =====================================================================
if __name__ == '__main__':
    # route_thread regression: ATS confirmation must NOT route as rejection.
    # This is the curriculumassociates@myworkday.com bug fix from 2026-05-09.
    assert route_thread(
        'Your application for Senior AI Enablement Specialist at Curriculum Associates',
        'curriculumassociates@myworkday.com',
    ) == 'skip', 'ATS confirmation subject must NOT route as rejection'

    # Subject phrase triggers rejection regardless of sender.
    assert route_thread(
        'We have decided not to move forward with your candidacy',
        'recruiter@somecompany.com',
    ) == 'rejection', 'canonical phrase must trigger rejection'

    # Configured sender routes to digest.
    assert route_thread(
        'Latest Job Postings',
        'ali@hiring.cafe',
    ) == 'digest', 'sender with configured parser must route digest'

    # Unconfigured sender, no rejection phrase → skip.
    assert route_thread(
        'Random newsletter',
        'unknown@example.com',
    ) == 'skip', 'unconfigured sender must skip'

    # Parsers must NOT stamp thread_id themselves — scan_digest does that
    # post-parse so the thread_id source of truth stays in one place.
    sample_dice_body = (
        '<https://www.dice.com/company/X>\n\n'
        'Test Role\n'
        '<https://www.dice.com/job-detail/abc123def456>\n\n'
        'Test Company\n\n'
        'Remote or NY, USA\n\n'
        'Posted: 05-09-2026\n'
    )
    parser_rows = parse_dice('Test', 'dice@connect.dice.com', sample_dice_body)
    assert len(parser_rows) == 1, 'parser should produce 1 row'
    assert 'thread_id' not in parser_rows[0], (
        'parser must not stamp thread_id; scan_digest owns that'
    )

    print('route_thread + parser-contract tests passed (5/5)')
