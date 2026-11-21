#!/usr/bin/env python3
"""Buzzweet manga-tweet page -> personal RSS feed.

The source website's markup is not an API; update parse_buzzweet if the
site changes its HTML. Fail safely rather than publishing an empty feed.
"""

from __future__ import annotations

import html
import json
import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup, NavigableString, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"
STATE_FILE = ROOT / "state.json"
RSS_FILE = SITE / "manga.xml"
STATUS_FILE = SITE / "status.json"
BASE_URL = "https://buzzweet.com/manga-tweet/"
FEED_TITLE = "Buzzweet 漫画ツイートRSS"
JST = timezone(timedelta(hours=9))
MAX_PAGES = 3
MAX_FEED_ITEMS = 250
MAX_SEEN_IDS = 5000
X_SNOWFLAKE_EPOCH_MS = 1288834974657
MEDIA_NS = "http://search.yahoo.com/mrss/"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
DC_NS = "http://purl.org/dc/elements/1.1/"
ET.register_namespace("media", MEDIA_NS)
ET.register_namespace("content", CONTENT_NS)
ET.register_namespace("dc", DC_NS)

POST_LINK = re.compile(r"^/(?:[A-Za-z0-9_]+|i/web)/status/(\d{10,25})(?:/|$)", re.I)
PROFILE_LINK = re.compile(r"^/([A-Za-z0-9_]{1,15})/?$")
TIME_MARK = re.compile(r"(?:\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}).{0,25}(?:時|件の表示)")
USERNAME_MARK = re.compile(r"^@[A-Za-z0-9_]{1,15}$")
NOISE = re.compile(r"^(?:話題の返信を見る|話題の返信|引用元ツイート|閉じる|×|表示|返信|いいね|リポスト|ブックマーク)$")


@dataclass
class Post:
    post_id: str
    text: str
    display_name: str
    username: str
    url: str
    image_url: str = ""
    first_seen: str = ""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def page_url(number: int) -> str:
    return BASE_URL if number == 1 else f"{BASE_URL}{number}/"


def http_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2, connect=2, read=1, status=2, backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",), respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; PersonalBuzzweetRSS/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.8",
    })
    return session


def valid_media_url(value: object, base: str) -> str:
    if not isinstance(value, str):
        return ""
    url = value.strip().strip('"\'')
    if not url or url.startswith(("data:", "javascript:", "blob:", "#")):
        return ""
    absolute = urljoin(base, html.unescape(url))
    parsed = urlparse(absolute)
    return absolute if parsed.scheme == "https" and parsed.netloc else ""


def status_id_from_link(href: str) -> str:
    u = urlparse(html.unescape(href))
    host = (u.hostname or "").lower()
    if host not in ("x.com", "www.x.com", "twitter.com", "www.twitter.com", "fxtwitter.com", "fixupx.com"):
        return ""
    found = POST_LINK.match(u.path)
    if found:
        return found.group(1)
    if u.path.lower().startswith("/intent/"):
        qs = parse_qs(u.query)
        for name in ("tweet_id", "in_reply_to"):
            ids = qs.get(name, [])
            if ids and re.fullmatch(r"\d{10,25}", ids[0]):
                return ids[0]
    return ""


def link_action(href: str) -> str:
    path = urlparse(href).path.lower()
    if "/intent/like" in path:
        return "like"
    if "/intent/retweet" in path:
        return "retweet"
    if "/intent/tweet" in path:
        return "reply"
    if "/status/" in path:
        return "status"
    return ""


def profile_from_anchor(a: Tag) -> tuple[str, str] | None:
    u = urlparse(a.get("href", ""))
    if (u.hostname or "").lower() not in ("x.com", "www.x.com", "twitter.com", "www.twitter.com"):
        return None
    m = PROFILE_LINK.match(u.path)
    if not m or m.group(1).lower() in ("home", "search", "explore", "intent", "share"):
        return None
    display_name = a.get_text(" ", strip=True)
    if not display_name or display_name.startswith("@"):
        return None
    return (m.group(1), display_name)


def card_for_like(like_anchor: Tag, post_id: str) -> Tag | None:
    """Find the smallest enclosing tweet card holding the author's profile
    and controls for this post. Never use the entire page as a tweet card.
    """
    candidate = like_anchor.parent
    for _ in range(13):
        if not isinstance(candidate, Tag) or candidate.name in ("body", "html", "main"):
            break
        anchors = candidate.select("a[href]")
        action_ids = {
            status_id_from_link(a["href"])
            for a in anchors if link_action(a["href"]) == "like"
        }
        action_ids.discard("")
        if action_ids == {post_id}:
            profiles = [profile_from_anchor(a) for a in anchors]
            profiles = [p for p in profiles if p]
            same_status = any(
                status_id_from_link(a["href"]) == post_id and
                link_action(a["href"]) in ("status", "retweet", "reply")
                for a in anchors
            )
            if profiles and same_status:
                return candidate
        candidate = candidate.parent
    return None


def in_nested_modal_or_quote(node: NavigableString, card: Tag) -> bool:
    node_parent = node.parent
    while isinstance(node_parent, Tag) and node_parent is not card:
        attrs = " ".join(node_parent.get("class", [])) + " " + str(node_parent.get("id", ""))
        if any(x in attrs.lower() for x in ("quote", "modal", "reply", "dialog", "popup", "retweet")):
            return True
        if node_parent.name in ("script", "style", "button", "svg", "noscript"):
            return True
        if node_parent.get("aria-hidden") == "true":
            return True
        node_parent = node_parent.parent
    return False


def post_body_from_card(card: Tag, author_name: str, username: str) -> str:
    """Collect visible text between the author's handle and post timestamp.

    Unlike get_text() this preserves the tweet text while discarding quoted
    posts, reaction counts, hidden modals, and toolbar labels.
    """
    parts: list[str] = []
    author_seen = False
    handle_seen = False
    for node in card.descendants:
        if not isinstance(node, NavigableString) or in_nested_modal_or_quote(node, card):
            continue
        value = str(node).strip()
        if not value:
            continue
        if not author_seen:
            if value == author_name:
                author_seen = True
            elif value.casefold() == ("@" + username).casefold():
                author_seen = True
                handle_seen = True
            continue
        if not handle_seen:
            if value.casefold() == ("@" + username).casefold():
                handle_seen = True
            elif TIME_MARK.search(value):
                return ""
            continue
        if TIME_MARK.search(value):
            break
        # Hashtags, URLs, and @mentions may be links within the post text.
        # Do not drop <a> text indiscriminately; only toolbar controls go.
        par = node.parent
        if par and par.name in ("time", "button"):
            continue
        if par and par.name == "a" and link_action(par.get("href", "")):
            continue
        if NOISE.match(value):
            continue
        parts.append(value)
    return "\n".join(parts).strip()


def thumbnail_from_card(card: Tag, base_url: str) -> str:
    candidates: list[str] = []
    # Prefer post media hosted by X over profile/avatar/site images.
    for el in card.select("img, source, video, [style], [data-src], [data-original]"):
        for attr in ("data-src", "data-original", "data-lazy-src", "src", "poster"):
            u = valid_media_url(el.get(attr), base_url)
            if u:
                candidates.append(u)
        style = el.get("style", "")
        for match in re.finditer(r"url\(\s*(?:\"([^\"]+)\"|'([^']+)'|([^)]*))\s*\)", style):
            raw = next((x for x in match.groups() if x), "")
            u = valid_media_url(raw, base_url)
            if u:
                candidates.append(u)
    for url in candidates:
        parsed = urlparse(url)
        if parsed.hostname in ("pbs.twimg.com", "video.twimg.com") and "/media/" in parsed.path:
            return url
    return ""


def parse_buzzweet(content: str, source_url: str) -> list[Post]:
    soup = BeautifulSoup(content, "html.parser")
    likes: list[tuple[str, Tag]] = []
    for a in soup.find_all("a", href=True):
        if link_action(a["href"]) == "like":
            pid = status_id_from_link(a["href"])
            if pid:
                likes.append((pid, a))
    if not likes:
        raise ValueError("X投稿の『いいね』リンクを検出できません。BuzzweetのHTML構造が変わった可能性があります。")

    posts: list[Post] = []
    seen_ids: set[str] = set()
    for pid, anchor in likes:
        if pid in seen_ids:
            continue
        card = card_for_like(anchor, pid)
        if card is None:
            logging.warning("投稿 %s: カードを特定できません", pid)
            continue
        profiles: list[tuple[str, str]] = []
        for a in card.select("a[href]"):
            p = profile_from_anchor(a)
            if p:
                profiles.append(p)
        if not profiles:
            continue
        username, name = profiles[0]
        # Prefer the username attached to this very status ID.
        for a in card.select("a[href]"):
            href = a["href"]
            if link_action(href) == "status" and status_id_from_link(href) == pid:
                user_match = POST_LINK.match(urlparse(href).path)
                if user_match:
                    candidate = urlparse(href).path.split("/")[1]
                    if candidate.lower() != "i":
                        username = candidate
                        for u, display in profiles:
                            if u.casefold() == username.casefold():
                                name = display
                                break
                        break
        body = post_body_from_card(card, name, username)
        if not body:
            # Empty-text picture posts are valid, but parse failures are not.
            body = "画像付き投稿"
        posts.append(Post(
            post_id=pid, text=body, display_name=name,
            username=username, url=f"https://x.com/{username}/status/{pid}",
            image_url=thumbnail_from_card(card, source_url),
        ))
        seen_ids.add(pid)

    if len(posts) < 3:
        raise ValueError(
            f"Buzzweetの投稿を{len(posts)}件しか解析できませんでした"
            f"（いいねリンク候補: {len(likes)}件）。RSSの上書きを中止します。"
        )
    return posts


def photo_from_fx_json(data: dict) -> str:
    status = data.get("status") or data.get("tweet")
    if not isinstance(status, dict):
        return ""
    media = status.get("media") or {}
    if not isinstance(media, dict):
        return ""
    photos = media.get("photos") or []
    if not isinstance(photos, list):
        photos = []
    for photo in photos:
        if isinstance(photo, dict):
            u = valid_media_url(photo.get("url"), "https://api.fxtwitter.com/")
            if u:
                return u
    for item in media.get("all") or []:
        if isinstance(item, dict) and item.get("type") == "photo":
            u = valid_media_url(item.get("url"), "https://api.fxtwitter.com/")
            if u:
                return u
    return ""


def enrich_with_fx(session: requests.Session, post: Post) -> Post:
    """Prefer FxEmbed's first actual attachment; keep Buzzweet fallback."""
    try:
        res = session.get(f"https://api.fxtwitter.com/2/status/{post.post_id}", timeout=13)
        res.raise_for_status()
        data = res.json()
        if isinstance(data, dict):
            image = photo_from_fx_json(data)
            if image:
                post.image_url = image
    except (requests.RequestException, ValueError, TypeError) as exc:
        logging.info("FxEmbed %s: %s (Buzzweet画像を維持)", post.post_id, exc)
    return post


def post_published_at(post: Post, fallback: datetime) -> datetime:
    """Use the original X post time encoded in its snowflake status ID.

    first_seen is kept separately for history/deduplication, not RSS sorting.
    """
    try:
        if post.post_id.isdecimal():
            created_ms = (int(post.post_id) >> 22) + X_SNOWFLAKE_EPOCH_MS
            created = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if datetime(2010, 11, 4, tzinfo=timezone.utc) <= created <= fallback + timedelta(days=1):
                return created
    except (OSError, OverflowError, ValueError):
        pass
    try:
        observed = datetime.fromisoformat(post.first_seen)
        return observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return fallback


def newest_first(posts: list[Post], now: datetime) -> list[Post]:
    return sorted(posts, key=lambda post: (post_published_at(post, now), post.post_id), reverse=True)


def xml_bytes(posts: list[Post], now: datetime) -> bytes:
    rss = ET.Element("rss", {"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = BASE_URL
    ET.SubElement(channel, "description").text = "Buzzweetの話題の漫画ツイートをまとめる非公式の個人用RSSです。"
    ET.SubElement(channel, "language").text = "ja"
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(now)
    ET.SubElement(channel, "generator").text = "BuzzweetMangaRSS"
    for post in newest_first(posts, now):
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = post.text or "画像付き投稿"
        ET.SubElement(item, "link").text = post.url
        ET.SubElement(item, "guid", {"isPermaLink": "false"}).text = f"buzzweet-x-{post.post_id}"
        ET.SubElement(item, "pubDate").text = format_datetime(post_published_at(post, now))
        summary = f"{post.display_name}｜{FEED_TITLE}" if post.display_name else FEED_TITLE
        ET.SubElement(item, "description").text = summary
        if post.display_name:
            ET.SubElement(item, f"{{{DC_NS}}}creator").text = post.display_name
        if post.image_url:
            ET.SubElement(item, f"{{{MEDIA_NS}}}thumbnail", {"url": post.image_url})
            ET.SubElement(item, f"{{{MEDIA_NS}}}content", {"url": post.image_url, "medium": "image"})
            # Some readers use content:encoded instead of media:thumbnail.
            body = f'<p>{html.escape(summary)}</p><p><img src="{html.escape(post.image_url, quote=True)}" alt="投稿画像" /></p>'
            ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = body
        else:
            ET.SubElement(item, f"{{{CONTENT_NS}}}encoded").text = f"<p>{html.escape(summary)}</p>"
    ET.indent(rss, space="  ")
    return ET.tostring(rss, xml_declaration=True, encoding="utf-8")


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSONが辞書形式ではありません: {path}")
    return data


def write_if_changed(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, str):
        content = content.encode("utf-8")
    if path.exists() and path.read_bytes() == content:
        return
    path.write_bytes(content)


def render_index(status: dict) -> str:
    last = html.escape(str(status.get("last_success_jst", "未取得")))
    count = html.escape(str(status.get("rss_items", "未取得")))
    outcome = "正常" if status.get("ok") else "取得失敗：前回RSSを維持"
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{FEED_TITLE}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,'Hiragino Kaku Gothic ProN',Meiryo,sans-serif;max-width:760px;margin:60px auto;padding:0 18px;line-height:1.8}}.muted{{color:#666}}</style></head>
<body><h1>{FEED_TITLE}</h1><p>Buzzweetの漫画ツイートをまとめる、非公式の個人用RSSです。</p>
<p><a href="manga.xml">Inoreader登録用RSS</a></p>
<p class="muted">最終取得成功日（日本時間）：{last} ／ {outcome} ／ 配信中の記事：{count}件</p>
<p><a href="{BASE_URL}">取得元：Buzzweet漫画ツイート一覧</a></p>
<p class="muted">Buzzweet・X・FxEmbedの公式サービスではありません。取得や画像の表示は保証されません。</p></body></html>\n'''


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / ".nojekyll").touch()
    now = now_utc()
    date_jst = now.astimezone(JST).date().isoformat()
    state = read_json(STATE_FILE, {})
    status = read_json(STATUS_FILE, {})
    seen = set(str(x) for x in state.get("seen_ids", []))
    old_posts: list[Post] = [Post(**x) for x in state.get("posts", [])]
    seen.update(p.post_id for p in old_posts)
    anchor_id = str(state.get("anchor_id", ""))
    full_scan = state.get("last_full_scan_jst") != date_jst
    logging.info("基準ID: %s / 日次3ページ全件確認: %s", anchor_id or "初回", full_scan)

    session = http_session()
    scanned: list[Post] = []
    current_ids: set[str] = set()
    pages_read = 0
    found_anchor = False
    first_page_newest = ""

    try:
        for page in range(1, MAX_PAGES + 1):
            url = page_url(page)
            response = session.get(url, timeout=30)
            response.raise_for_status()
            entries = parse_buzzweet(response.text, url)
            pages_read += 1
            if page == 1:
                first_page_newest = entries[0].post_id
            for entry in entries:
                if entry.post_id in current_ids:
                    continue
                current_ids.add(entry.post_id)
                scanned.append(entry)
                if entry.post_id == anchor_id:
                    found_anchor = True
            logging.info("%dページ目: %d件 / 基準ID発見: %s", page, len(entries), found_anchor)
            if found_anchor and not full_scan:
                break
            time.sleep(1.2)
        if not scanned or not first_page_newest:
            raise ValueError("投稿を取得できませんでした")
    except (requests.RequestException, ValueError) as exc:
        logging.error("Buzzweet取得失敗: %s", exc)
        print("::warning::Buzzweet取得失敗。既存RSSを上書きしません。", flush=True)
        if not RSS_FILE.exists():
            return 1
        failed_status = dict(status)
        failed_status["ok"] = False
        failed_status["error"] = str(exc)[:220]
        write_if_changed(STATUS_FILE, json.dumps(failed_status, ensure_ascii=False, indent=2) + "\n")
        write_if_changed(SITE / "index.html", render_index(failed_status))
        return 0

    if anchor_id and not found_anchor:
        logging.warning("基準ID %s は最大%dページで未発見。取得済み投稿は保存します。", anchor_id, MAX_PAGES)
        print("::warning::前回最新投稿が見つかりませんでした。投稿の移動・削除または取得漏れの可能性があります。", flush=True)

    fresh = [p for p in scanned if p.post_id not in seen]
    logging.info("未登録の新規投稿: %d件 / 前回からの目印: %s", len(fresh), first_page_newest)
    enriched_count = 0
    for post in fresh:
        enrich_with_fx(session, post)
        if post.image_url:
            enriched_count += 1
        # Avoid burst requests to the third-party Fx API.
        time.sleep(0.3)
    logging.info("新規投稿のサムネイル取得: %d/%d件", enriched_count, len(fresh))

    # Keep first_seen for discovery history, but publish and sort by X post time.
    # X snowflake timestamps work even when multiple posts are first seen together.
    for post in fresh:
        post.first_seen = now.isoformat()
    fresh_ids = {p.post_id for p in fresh}
    all_posts = newest_first(fresh + [p for p in old_posts if p.post_id not in fresh_ids], now)[:MAX_FEED_ITEMS]
    recent_seen = list(dict.fromkeys([p.post_id for p in scanned] + list(state.get("seen_ids", []))))[:MAX_SEEN_IDS]

    latest_state = {
        "anchor_id": first_page_newest,
        "last_full_scan_jst": date_jst if full_scan else state.get("last_full_scan_jst", ""),
        "seen_ids": recent_seen,
        "posts": [asdict(x) for x in all_posts],
    }
    good_status = {
        "ok": True,
        "last_success_jst": now.astimezone(JST).strftime("%Y-%m-%d %H:%M"),
        "rss_items": len(all_posts),
        "pages_scanned": pages_read,
        "new_posts": len(fresh),
        "new_posts_with_image": enriched_count,
        "anchor_found": found_anchor,
    }

    write_if_changed(STATE_FILE, json.dumps(latest_state, ensure_ascii=False, indent=2) + "\n")
    write_if_changed(RSS_FILE, xml_bytes(all_posts, now))
    write_if_changed(STATUS_FILE, json.dumps(good_status, ensure_ascii=False, indent=2) + "\n")
    write_if_changed(SITE / "index.html", render_index(good_status))
    logging.info("RSS生成成功: 合計%d件、今回新規%d件、巡回%dページ", len(all_posts), len(fresh), pages_read)
    return 0


if __name__ == "__main__":
    sys.exit(main())
