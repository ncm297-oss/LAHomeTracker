"""Gmail adapter: read Redfin / Zillow / MLS-portal alert emails (read-only) and turn
them into listings + price points, and saved rental-search alerts into rentals. The parser is a pure function so it is testable
with fixture emails without any Google client."""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from html import unescape
from pathlib import Path

from ..config import ROOT, neighborhood_for, secret
from ..models import Listing, PricePoint, Rental
from ..normalize import rental_kind
from .base import SourceAdapter, SourceError

log = logging.getLogger("tracker")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
TOKEN_PATH = ROOT / "token.json"
CREDENTIALS_PATH = ROOT / "credentials.json"

_STREET = r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Pl|Place|Ct|Court|Ln|Lane|Way|Ter|Terrace|Cir|Circle|Pkwy|Walk|Hwy|Prom|Promenade)"
_ADDR_RE = re.compile(
    # The lookbehind stops a match starting inside a token such as a tracking URL ending in digits.
    rf"(?<![\w/=?&%.#-])(\d{{2,6}}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][A-Za-z0-9'.\- ]{{1,40}}?\s{_STREET}\.?(?:\s*(?:#|Unit|UNIT|Apt|APT)\s*[A-Za-z0-9-]+)?)"
    r",?\s+([A-Z][A-Za-z .]{2,30}?),?\s+CA\s*(\d{5})",
)
_PRICE_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{6,8})(?:\.\d+)?(?!\s*/\s*mo)")
_WAS_RE = re.compile(r"(?:was|from|previously|down from)\s*\$\s?(\d{1,3}(?:,\d{3})+)", re.I)
_URL_RE = re.compile(r"https?://(?:www\.)?(?:redfin|zillow)\.com/[^\s\"'<>)]+")
_MLS_RE = re.compile(r"MLS\s*#?\s*:?\s*([A-Z]{0,3}\d[\d-]{4,14}\d)", re.I)


def html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    # keep link targets in the text so the parser can attach a listing URL
    html = re.sub(r"(?is)<a\s[^>]*href=[\"']([^\"']+)[\"'][^>]*>", r" \1 ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>|</h\d>", "\n", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    return re.sub(r"[ \t\xa0]+", " ", text)


def parse_alert_text(text: str, source: str, received: str | None = None) -> list[Listing]:
    """Find (address, price, [old price], [url]) groups in an alert email body."""
    received = received or datetime.now().date().isoformat()
    out: list[Listing] = []
    seen: set[str] = set()
    for m in _ADDR_RE.finditer(text):
        addr, city, zip5 = m.group(1).strip(), m.group(2).strip(), m.group(3)
        window = text[m.end(): m.end() + 400]
        before = text[max(0, m.start() - 200): m.start()]
        pm = _PRICE_RE.search(window) or _PRICE_RE.search(before)
        if not pm:
            continue
        price = float(pm.group(1).replace(",", ""))
        if price < 100_000:
            continue
        key = f"{addr}|{zip5}"
        if key in seen:
            continue
        seen.add(key)
        lst = Listing(address=addr, city=city, zip_code=zip5, list_price=price, sources=[source])
        lst.neighborhood = neighborhood_for(zip5, city)
        um = _URL_RE.search(window) or _URL_RE.search(before)
        if um:
            lst.source_url = um.group(0)
        mm = _MLS_RE.search(window)
        if mm:
            lst.mls_number = mm.group(1)
        wm = _WAS_RE.search(window)
        if wm:
            old = float(wm.group(1).replace(",", ""))
            if old > price:
                lst.original_list_price = old
                lst.price_history.append(PricePoint(received, old, "list", source))
                lst.price_history.append(PricePoint(received, price, "cut", source))
        else:
            lst.price_history.append(PricePoint(received, price, "list", source))
        out.append(lst)
    return out


# Monthly rents: "$6,850/mo", "$6,850", "$6850+". The lookahead keeps "$2,199,000" from reading as $2,199.
_RENT_RE = re.compile(r"\$\s?(\d{1,2},\d{3}|\d{4,5})(?:\.\d{2})?(?![\d,])\+?")
_BEDS_RE = re.compile(r"(\d+)\s*(?:bds?|beds?|br|bedrooms?)\b", re.I)
_BATHS_RE = re.compile(r"(\d+(?:\.\d)?)\s*(?:ba|baths?|bathrooms?)\b", re.I)
_SQFT_RE = re.compile(r"(\d{1,2},\d{3}|\d{3,5})\s*(?:sq\.?\s*ft\.?|sqft|sf|square feet)", re.I)
_KIND_RE = re.compile(r"\b(house|single[- ]family(?: home)?|townhouse|townhome|condo|duplex|apartment)\b(?:\s+for\s+rent)?", re.I)
_RENT_SITES = r"zillow|hotpads|trulia|apartments|westsiderentals|redfin|zumper|realtor|homes"
_RENT_URL_RE = re.compile(rf"https?://(?:[\w-]+\.)*(?:{_RENT_SITES})\.com/[^\s\"'<>)]+", re.I)


def rental_source_for(sender: str) -> str:
    s = (sender or "").lower()
    for key, name in (("zillow", "zillow"), ("hotpads", "hotpads"), ("trulia", "trulia"), ("westsiderentals", "apartments.com"),
                      ("apartments.com", "apartments.com"), ("redfin", "redfin"), ("zumper", "zumper"), ("realtor", "realtor.com")):
        if key in s:
            return name
    return "email"


def _first_rent(window: str):
    for rm in _RENT_RE.finditer(window):
        v = float(rm.group(1).replace(",", ""))
        if 1000 <= v <= 30000:
            return rm, v
    return None, None


def parse_rental_alert_text(text: str, source: str, cfg: dict | None = None) -> list[Rental]:
    """Find (address, monthly rent, [beds/baths/sqft/type], [url]) groups in a rental alert.

    Alerts put a home's details either below its address (Zillow) or above it (Apartments.com),
    so the text between two addresses belongs to one or the other. The layout is read once per
    email from the edges: a rent before the first address and none after the last means details
    sit above their address."""
    out: list[Rental] = []
    seen: set[str] = set()
    matches = list(_ADDR_RE.finditer(text))
    if not matches:
        return out
    afters = [text[m.end(): matches[i + 1].start() if i + 1 < len(matches) else m.end() + 500][:500] for i, m in enumerate(matches)]
    befores = [text[max(matches[i - 1].end() if i else 0, m.start() - 250): m.start()] for i, m in enumerate(matches)]
    above = _first_rent(befores[0])[1] is not None and _first_rent(afters[-1])[1] is None
    for i, m in enumerate(matches):
        addr, city, zip5 = m.group(1).strip(), m.group(2).strip(), m.group(3)
        after, before = afters[i], befores[i]
        win = before if above else after
        rm, rent = _first_rent(win)
        if not rent:
            continue
        key = f"{addr}|{zip5}"
        if key in seen:
            continue
        seen.add(key)

        def num(rx):
            mm = rx.search(win)
            return float(mm.group(1).replace(",", "")) if mm else None
        km = _KIND_RE.search(win)
        label = km.group(1) if km else None
        # html_to_text writes a link's target just before its text, so an address that is itself
        # the link has its URL immediately in front of it. Otherwise take the first URL after the
        # address, stopping at the next listing's rent when details sit above addresses.
        linked = [u for u in _RENT_URL_RE.finditer(before) if len(before) - u.end() <= 3]
        tail = after
        if above:
            nxt = _first_rent(after)[0]
            tail = after[: nxt.start()] if nxt else after
        um = linked[-1] if linked else _RENT_URL_RE.search(tail)
        out.append(Rental(
            address=addr, source=source, city=city, zip_code=zip5, neighborhood=neighborhood_for(zip5, city, cfg),
            rent=rent, beds=num(_BEDS_RE), baths=num(_BATHS_RE), sqft=num(_SQFT_RE),
            property_type=label, kind=rental_kind(label, None, addr),
            url=um.group(0) if um else None,
        ))
    return out


def source_for(sender: str) -> str:
    s = (sender or "").lower()
    if "redfin" in s:
        return "gmail:redfin"
    if "zillow" in s:
        return "gmail:zillow"
    return "gmail:mls"


def load_credentials():
    """OAuth credentials from token.json, or the GMAIL_TOKEN_JSON secret (raw or base64)."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as e:
        raise SourceError("google-api-python-client / google-auth-oauthlib not installed (pip install .[gmail])") from e
    raw = secret("GMAIL_TOKEN_JSON")
    info = None
    if raw:
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = json.loads(base64.b64decode(raw).decode())
    elif TOKEN_PATH.exists():
        info = json.loads(TOKEN_PATH.read_text())
    if not info:
        raise SourceError("No Gmail token: run `python -m tracker gmail-auth` first")
    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        if TOKEN_PATH.exists() and not raw:
            TOKEN_PATH.write_text(creds.to_json())
    return creds


def run_oauth_flow() -> Path:
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CREDENTIALS_PATH.exists():
        raise SourceError(f"Put your Google OAuth client file at {CREDENTIALS_PATH} (Desktop app credentials)")
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    return TOKEN_PATH


class GmailAlerts(SourceAdapter):
    name = "gmail"

    def service(self):
        try:
            from googleapiclient.discovery import build
        except ImportError as e:
            raise SourceError("google-api-python-client not installed (pip install .[gmail])") from e
        return build("gmail", "v1", credentials=load_credentials(), cache_discovery=False)

    @staticmethod
    def _body(payload: dict) -> str:
        """Prefer text/plain, else text/html converted."""
        texts, htmls = [], []

        def walk(part):
            mime = part.get("mimeType", "")
            data = (part.get("body") or {}).get("data")
            if data:
                decoded = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
                (texts if mime == "text/plain" else htmls if mime == "text/html" else []).append(decoded)
            for p in part.get("parts", []) or []:
                walk(p)
        walk(payload)
        if texts:
            return "\n".join(texts)
        return html_to_text("\n".join(htmls))

    def _messages(self, query: str | None, max_messages: int = 100):
        """Yield (headers, received_date, body) for messages matching a Gmail search."""
        svc = self.service()
        resp = svc.users().messages().list(userId="me", q=query, maxResults=max_messages).execute()
        self.count("messages.list")
        for m in resp.get("messages", []) or []:
            msg = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            self.count("messages.get")
            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            received = datetime.fromtimestamp(int(msg["internalDate"]) / 1000).date().isoformat()
            yield headers, received, self._body(msg["payload"])

    def fetch_alerts(self, max_messages: int = 100) -> list[Listing]:
        out: list[Listing] = []
        for headers, received, body in self._messages(self.scfg.get("query"), max_messages):
            src = source_for(headers.get("from", ""))
            found = parse_alert_text(f"{headers.get('subject', '')}\n{body}", src, received)
            log.info("gmail: %s from %s -> %d listings", headers.get("subject", "")[:60], src, len(found))
            out.extend(found)
        return out

    def fetch_rental_alerts(self, max_messages: int = 100) -> list[Rental]:
        out: list[Rental] = []
        for headers, _received, body in self._messages(self.scfg.get("rental_query"), max_messages):
            src = rental_source_for(headers.get("from", ""))
            found = parse_rental_alert_text(f"{headers.get('subject', '')}\n{body}", src, self.cfg)
            log.info("gmail rentals: %s from %s -> %d homes", headers.get("subject", "")[:60], src, len(found))
            out.extend(found)
        return out
