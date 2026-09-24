"""Gmail adapter: read Redfin / Zillow / MLS-portal alert emails (read-only) and turn
them into listings + price points. The parser is a pure function so it is testable
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
from ..models import Listing, PricePoint
from .base import SourceAdapter, SourceError

log = logging.getLogger("tracker")

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]
TOKEN_PATH = ROOT / "token.json"
CREDENTIALS_PATH = ROOT / "credentials.json"

_STREET = r"(?:St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Rd|Road|Pl|Place|Ct|Court|Ln|Lane|Way|Ter|Terrace|Cir|Circle|Pkwy|Walk|Hwy|Prom|Promenade)"
_ADDR_RE = re.compile(
    rf"(\d{{2,6}}\s+(?:[NSEW]\.?\s+)?[A-Z0-9][A-Za-z0-9'.\- ]{{1,40}}?\s{_STREET}\.?(?:\s*(?:#|Unit|Apt)\s*[A-Za-z0-9-]+)?)"
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

    def fetch_alerts(self, max_messages: int = 100) -> list[Listing]:
        svc = self.service()
        q = self.scfg.get("query")
        resp = svc.users().messages().list(userId="me", q=q, maxResults=max_messages).execute()
        self.count("messages.list")
        out: list[Listing] = []
        for m in resp.get("messages", []) or []:
            msg = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            self.count("messages.get")
            headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
            src = source_for(headers.get("from", ""))
            received = datetime.fromtimestamp(int(msg["internalDate"]) / 1000).date().isoformat()
            body = self._body(msg["payload"])
            found = parse_alert_text(f"{headers.get('subject', '')}\n{body}", src, received)
            log.info("gmail: %s from %s -> %d listings", headers.get("subject", "")[:60], src, len(found))
            out.extend(found)
        return out
