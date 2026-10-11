"""Sharp Market News: RSS -> Discord. Python 3.12+.

No trade recommendations. This is a delayed/aggregated news notifier, not a live newswire.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree
from email.utils import parsedate_to_datetime

import requests

# Publisher feeds only; never include user-provided secrets in URLs.
FEEDS = (
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories"),
    ("Yahoo Finance — SPY", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("Google News — Markets", "https://news.google.com/rss/search?q=stock%20market%20OR%20Federal%20Reserve%20OR%20Nasdaq%20when%3A1d&hl=en-US&gl=US&ceid=US%3Aen"),
)
STATE_FILE = Path(".news_seen.json")
MAX_AGE_SECONDS = 3 * 3600
MAX_POSTS_PER_RUN = 3
STATE_TTL_SECONDS = 4 * 86400

# Exact-looking headline filters are only a *heuristic*, not a measure of actual impact.
IMPORTANT_PATTERNS = (
    r"\bfomc statement\b",
    r"\bfederal reserve (?:raises|cuts|holds|announces)\b",
    r"\bfed (?:raises|cuts|holds) (?:interest )?rates\b",
    r"\binterest.rate (?:decision|cut|hike)\b",
    r"\btrading (?:halt|suspend)\b",
    r"\bfiles? for bankruptcy\b",
    r"\b(?:cpi|consumer price index|nonfarm payrolls?|jobs report|unemployment rate)\b.*\b(?:surges?|jumps?|falls?|drops?|unexpected|above estimates|below estimates)\b",
    r"\b(?:trading halted|exchange halt|market.wide halt)\b",
)

@dataclass(frozen=True)
class Headline:
    title: str
    link: str
    source: str
    published: int


# These intentionally prefer missing a marginal headline over spamming members.
# This is headline-based filtering, not a real-time market-impact classifier.
PERSONAL_FINANCE_PATTERNS = (
    r"\bmy (?:aunt|uncle|mother|father|mom|dad|parents|husband|wife|daughter|son|sister|brother)\b",
    r"\b(?:i have no children|how much should i|should i retire|my retirement|my inheritance)\b",
    r"\b(?:mortgage tips|credit card rewards|personal finance|401\(?k\)?|down payment|estate planning)\b",
)
CLICKBAIT_PATTERNS = (
    r"\b(?:stocks? to buy|best stocks?|top \d+ stocks?|near buy points?|buy points?|stocks? you should buy)\b",
    r"\b(?:could make you rich|millionaire maker|before it's too late|you won't believe|must own|hot stock picks?)\b",
    r"\b(?:price prediction|what to buy now|should you buy|is it time to buy|worth buying)\b",
)
MACRO_PATTERNS = (
    r"\b(?:federal reserve|fomc|jerome powell|fed chair|fed (?:raises|cuts|holds|keeps) rates|interest.rate|rate cut|rate hike|rate decision)\b",
    r"\b(?:cpi|ppi|consumer price index|producer price index|inflation|nonfarm payrolls?|payrolls?|jobless claims|unemployment rate|jobs report|gdp)\b",
    r"\b(?:treasury yields?|bond yields?|10.year treasury|treasury auction)\b",
)
MARKET_PATTERNS = (
    r"\b(?:s&p 500|nasdaq|dow jones|wall street|stock futures|u\.?s\.? stocks?|russell 2000|equities|stock market|oil prices?|crude oil|gold futures)\b",
)
MARKET_MOVEMENT_PATTERNS = (
    r"\b(?:rise|rises|rose|rally|rallies|surge|surges|jump|jumps|climb|climbs|fall|falls|fell|drop|drops|slump|slumps|plunge|plunges|selloff|sell.off|slide|slides|rebound|rebounded|sink|sinks|tumble|tumbles|halt|halts|close|closed|open|opens|record|highs|lows)\b",
)
COMPANY_PATTERNS = (
    r"\b(?:nvidia|amd|advanced micro devices|micron|broadcom|apple|microsoft|alphabet|google|amazon|meta|tesla|robinhood|coinbase|palantir|netflix|jpmorgan|bank of america|exxon|intel|berkshire)\b",
)
CATALYST_PATTERNS = (
    r"\b(?:earnings|revenue|profit warning|profit forecast|guidance|outlook|quarterly results?|forecasts?|cuts? forecasts?|raises? forecasts?|beats? estimates?|misses? estimates?|dividend|stock split|merger|acquisition|takeover|ipo|bankruptcy|sec investigation|antitrust|fda approval|recall|trading halt|suspend|suspends|downgrade|upgrade|ceo resigns?|investigation|settlement)\b",
)


def has_match(patterns: tuple[str, ...], title: str) -> bool:
    return any(re.search(pattern, title, re.IGNORECASE) for pattern in patterns)


def is_relevant(item: Headline) -> bool:
    """Require a market-moving topic or stock catalyst; reject personal-finance clickbait."""
    title = item.title
    if has_match(PERSONAL_FINANCE_PATTERNS, title) or has_match(CLICKBAIT_PATTERNS, title):
        return False
    if has_match(MACRO_PATTERNS, title):
        return True
    if item.source == "Federal Reserve":
        # Official feed is limited to monetary-policy press releases.
        return True
    if has_match(MARKET_PATTERNS, title) and (has_match(MARKET_MOVEMENT_PATTERNS, title) or has_match(CATALYST_PATTERNS, title)):
        return True
    if has_match(CATALYST_PATTERNS, title) and (has_match(COMPANY_PATTERNS, title) or re.search(r"\b(?:stocks?|shares|company|corporation)\b", title, re.IGNORECASE)):
        return True
    return False


def clean_title(raw: str) -> str:
    text = re.sub(r"<[^>]*>", "", str(raw))
    return " ".join(text.replace("\x00", " ").split())[:250]


def fingerprint(item: Headline) -> str:
    # Title makes syndication across different news feeds count as a single story.
    canonical = re.sub(r"[^a-z0-9]+", " ", item.title.lower()).strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def parse_when(raw: str) -> int:
    if not raw:
        return 0
    try:
        return int(parsedate_to_datetime(raw).timestamp())
    except (ValueError, TypeError, OverflowError):
        try:
            return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0


def child_text(element: ElementTree.Element, *names: str) -> str:
    for name in names:
        node = element.find(name)
        if node is not None and node.text:
            return node.text.strip()
    return ""


def parse_rss(content: bytes, name: str, now: int) -> list[Headline]:
    try:
        root = ElementTree.fromstring(content)
    except ElementTree.ParseError as exc:
        raise ValueError(f"Invalid RSS/XML: {exc}") from exc
    items = root.findall(".//item")
    atom = False
    if not items:
        items = root.findall("{http://www.w3.org/2005/Atom}entry")
        atom = True
    if not items:
        raise ValueError("Feed had no readable entries (may be blocked or empty)")
    output: list[Headline] = []
    for entry in items:
        if atom:
            title = child_text(entry, "{http://www.w3.org/2005/Atom}title")
            date = child_text(entry, "{http://www.w3.org/2005/Atom}published", "{http://www.w3.org/2005/Atom}updated")
            link = ""
            for node in entry.findall("{http://www.w3.org/2005/Atom}link"):
                if node.attrib.get("rel", "alternate") == "alternate":
                    link = node.attrib.get("href", "")
                    break
        else:
            title = child_text(entry, "title")
            date = child_text(entry, "pubDate", "{http://purl.org/dc/elements/1.1/}date")
            link = child_text(entry, "link")
        title = clean_title(title)
        timestamp = parse_when(date)
        if not title or not link.startswith("https://"):
            continue
        if not (now - MAX_AGE_SECONDS <= timestamp <= now + 300):
            continue
        output.append(Headline(title, link, name, timestamp))
    return output


def fetch_feed(session: requests.Session, name: str, url: str, now: int) -> list[Headline]:
    response = session.get(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; SharpMarketNews/1.0; RSS reader)", "Accept": "application/rss+xml, application/atom+xml, text/xml, application/xml"},
        timeout=15,
    )
    response.raise_for_status()
    return parse_rss(response.content, name, now)


def load_seen() -> dict[str, int]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state is not a map")
        return {str(k): int(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {}


def save_seen(seen: dict[str, int], now: int) -> None:
    seen = {k: v for k, v in seen.items() if v >= now - STATE_TTL_SECONDS}
    STATE_FILE.write_text(json.dumps(seen, sort_keys=True), encoding="utf-8")


def is_important(item: Headline) -> bool:
    low = item.title.lower()
    return any(re.search(p, low) for p in IMPORTANT_PATTERNS)


def discord_payload(item: Headline, role_id: str, ping_mode: str) -> dict:
    priority = is_important(item)
    ping = bool(role_id and (ping_mode == "all" or (ping_mode == "important" and priority)))
    note = "📢 Potentially important headline" if priority else "📰 Market headline"
    # User-controlled feeds may contain Discord mentions, so disable all mentions by default.
    desc = (
        f"**{note}**\n"
        f"**{item.title.replace('`', '')}**\n"
        f"Source feed: {item.source} · Published: <t:{item.published}:t>\n"
        f"{item.link}\n"
        "_Headline only. Check the original report and chart; this isn't a trade signal._"
    )
    prefix = f"<@&{role_id}>\n" if ping else ""
    result = {"username": "Sharp Market News", "content": (prefix + desc)[:1900], "allowed_mentions": {"parse": [], "roles": [role_id] if ping else []}}
    return result


def post_webhook(session: requests.Session, webhook: str, payload: dict) -> None:
    if not webhook.startswith("https://discord.com/api/webhooks/") and not webhook.startswith("https://discordapp.com/api/webhooks/"):
        raise ValueError("DISCORD_WEBHOOK_URL must be a Discord webhook URL")
    r = session.post(webhook, json=payload, timeout=15)
    r.raise_for_status()


def run() -> int:
    mode = os.getenv("RUN_MODE", "preview").lower()
    if mode not in {"test", "preview", "live"}:
        raise ValueError("RUN_MODE must be test, preview, or live")
    webhook = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    role_id = os.getenv("DISCORD_ROLE_ID", "").strip()
    ping_mode = os.getenv("PING_MODE", "important").lower()
    if ping_mode not in {"important", "all", "none"}:
        raise ValueError("PING_MODE must be important, all, or none")
    if role_id and not role_id.isdigit():
        raise ValueError("DISCORD_ROLE_ID must be numeric")

    session = requests.Session()
    if mode == "test":
        if not webhook:
            raise ValueError("DISCORD_WEBHOOK_URL secret is missing")
        post_webhook(session, webhook, {"username": "Sharp Market News", "content": "🧪 **TEST ONLY — Sharp Market News is connected.** No feeds retrieved and no role ping sent.", "allowed_mentions": {"parse": []}})
        print("Discord test message sent (no role ping).")
        return 0

    now = int(time.time())
    stories: list[Headline] = []
    sources_ok = 0
    for source, url in FEEDS:
        try:
            fetched = fetch_feed(session, source, url, now)
            sources_ok += 1
            stories.extend(fetched)
            print(f"{source}: {len(fetched)} recent headlines")
        except (requests.RequestException, ValueError) as exc:
            print(f"WARNING — {source}: {type(exc).__name__}: {exc}", file=sys.stderr)
    if sources_ok == 0:
        raise RuntimeError("All RSS feeds failed; no news will be posted. Inspect network access and feed URLs.")

    deduped: dict[str, Headline] = {}
    for article in sorted(stories, key=lambda x: x.published, reverse=True):
        deduped.setdefault(fingerprint(article), article)
    unique = sorted(deduped.values(), key=lambda x: x.published, reverse=True)
    relevant = [article for article in unique if is_relevant(article)]
    print(f"Headline relevance filter: kept {len(relevant)} of {len(unique)} unique recent headlines; excluded {len(unique) - len(relevant)} off-topic or low-signal headlines.")

    if mode == "preview":
        print("PREVIEW — no Discord messages and no state changes.")
        print("QUALIFIED HEADLINES:")
        for article in relevant[:8]:
            when = datetime.fromtimestamp(article.published, tz=timezone.utc).isoformat()
            print(f"{when} | {article.source} | {article.title} | {'IMPORTANT' if is_important(article) else 'routine'}")
        if not relevant:
            print("No headlines passed the relevance filter. This is OK; no posts are necessary.")
        print("EXCLUDED EXAMPLES:")
        for article in [article for article in unique if not is_relevant(article)][:5]:
            print(f"  excluded: {article.title}")
        return 0

    if not webhook:
        raise ValueError("DISCORD_WEBHOOK_URL secret is missing")
    seen = load_seen()
    previous_state = STATE_FILE.exists()
    # First live run seeds stories silently, preventing a flood of initial posts.
    if not previous_state:
        for article in unique:
            seen[fingerprint(article)] = now
        save_seen(seen, now)
        print(f"First live run: seeded {len(unique)} headlines (including excluded ones), no Discord posts. Next run posts new qualified news.")
        return 0

    pending = [article for article in relevant if fingerprint(article) not in seen]
    # Prioritize important alerts, then newest, without claiming everything is actually impactful.
    pending.sort(key=lambda article: (is_important(article), article.published), reverse=True)
    posted = 0
    # Do not mark unposted articles as sent. They remain eligible next run while recent.
    for article in pending[:MAX_POSTS_PER_RUN]:
        post_webhook(session, webhook, discord_payload(article, role_id, ping_mode))
        seen[fingerprint(article)] = now
        posted += 1
    save_seen(seen, now)
    print(f"Posted {posted} new news alerts to Discord; {max(len(pending) - posted,0)} queued for a future run while recent.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except (requests.RequestException, RuntimeError, ValueError) as error:
        print(f"ERROR — {error}", file=sys.stderr)
        raise SystemExit(1)
