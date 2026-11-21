import json
import unittest
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

from generate import (
    Post, card_for_like, page_url, parse_buzzweet,
    photo_from_fx_json, status_id_from_link, xml_bytes,
)


HTML = '''<!doctype html><html><body>
<h1>話題の漫画ツイート</h1>
<div class="tweet-card">
  <div class="tweet-header"><a href="https://x.com/artist_A">作家A</a><span>@artist_A</span></div>
  <div class="tweet-content">カフェの漫画を描きました。<br>2ページ目です！</div>
  <div class="tweet-image"><img src="https://pbs.twimg.com/media/test-A.jpg" alt="manga"></div>
  <div class="tweet-date">11月21日・12時00分・41.6万件の表示</div>
  <div class="tweet-actions">
    <a href="https://x.com/intent/tweet?in_reply_to=2085923960588234759">返信</a>
    <a href="https://x.com/intent/retweet?tweet_id=2085923960588234759">リポスト</a>
    <a href="https://x.com/intent/like?tweet_id=2085923960588234759">いいね</a>
    <a href="https://x.com/artist_A/status/2085923960588234759">共有する</a>
  </div>
</div>
<div class="tweet-card">
  <a href="https://x.com/artist_B">作家B</a><span>@artist_B</span>
  <p>おもしろい話</p>
  <div class="quoted-post"><a href="https://x.com/quoted_author">引用元</a><span>@quoted_author</span>
    <span>引用元ツイート本文</span><a href="https://x.com/quoted_author/status/2085901504700133405">引用</a>
  </div>
  <div>11月21日・11時30分・300万件の表示</div>
  <a href="https://x.com/intent/tweet?in_reply_to=2085901504700133406">返信</a>
  <a href="https://x.com/intent/retweet?tweet_id=2085901504700133406">RT</a>
  <a href="https://x.com/intent/like?tweet_id=2085901504700133406">Like</a>
</div>
<div class="tweet-card">
  <a href="https://x.com/artist_C">作家C</a><span>@artist_C</span>
  <p>4コマ漫画</p>
  <p>11月21日・10時00分・20万件の表示</p>
  <a href="https://x.com/intent/tweet?in_reply_to=2085901504700133407">返信</a>
  <a href="https://x.com/intent/retweet?tweet_id=2085901504700133407">RT</a>
  <a href="https://x.com/intent/like?tweet_id=2085901504700133407">Like</a>
</div>
<aside><a href="https://x.com/intent/like?tweet_id=1111111111111111111">aside noise</a></aside>
</body></html>'''


class ParsingTests(unittest.TestCase):
    def test_status_id(self):
        self.assertEqual(status_id_from_link("https://x.com/a/status/2085923960588234759"), "2085923960588234759")
        self.assertEqual(status_id_from_link("https://x.com/intent/like?tweet_id=2085923960588234759"), "2085923960588234759")
        self.assertEqual(status_id_from_link("https://x.com/intent/tweet?in_reply_to=2085923960588234759"), "2085923960588234759")
        self.assertEqual(status_id_from_link("https://x.com/artist_A"), "")

    def test_page_urls(self):
        self.assertEqual(page_url(1), "https://buzzweet.com/manga-tweet/")
        self.assertEqual(page_url(2), "https://buzzweet.com/manga-tweet/2/")
        self.assertEqual(page_url(3), "https://buzzweet.com/manga-tweet/3/")

    def test_parse_different_authors_and_quote_not_separate(self):
        posts = parse_buzzweet(HTML, page_url(1))
        self.assertEqual(len(posts), 3)
        self.assertEqual(posts[0].display_name, "作家A")
        self.assertEqual(posts[0].text, "カフェの漫画を描きました。\n2ページ目です！")
        self.assertEqual(posts[0].image_url, "https://pbs.twimg.com/media/test-A.jpg")
        self.assertEqual(posts[1].display_name, "作家B")
        self.assertEqual(posts[1].text, "おもしろい話")
        self.assertNotIn("引用元", posts[1].text)
        self.assertTrue(posts[2].url.startswith("https://x.com/artist_C/status/"))

    def test_fx_media_first_photo(self):
        self.assertEqual(photo_from_fx_json({"status": {"media": {"photos": [
            {"type": "photo", "url": "https://pbs.twimg.com/media/photo.jpg"},
            {"type": "photo", "url": "https://pbs.twimg.com/media/photo2.jpg"},
        ]}}}), "https://pbs.twimg.com/media/photo.jpg")
        self.assertEqual(photo_from_fx_json({"status": {"media": {"photos": []}}}), "")

    def test_rss_title_and_image(self):
        posts = parse_buzzweet(HTML, page_url(1))
        for x in posts:
            x.first_seen = "2026-11-21T06:00:00+00:00"
        rss = xml_bytes(posts, datetime(2026, 11, 21, tzinfo=timezone.utc))
        root = ET.fromstring(rss)
        items = root.findall("./channel/item")
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].findtext("title"), "カフェの漫画を描きました。\n2ページ目です！")
        self.assertEqual(items[0].findtext("link"), "https://x.com/artist_A/status/2085923960588234759")
        self.assertEqual(items[0].findtext("description"), "作家A｜Buzzweet 漫画ツイートRSS")
        self.assertEqual(items[0].find("{http://search.yahoo.com/mrss/}thumbnail").attrib["url"], "https://pbs.twimg.com/media/test-A.jpg")
        self.assertIn("<img ", items[0].findtext("{http://purl.org/rss/1.0/modules/content/}encoded"))
        self.assertEqual(items[0].findtext("{http://purl.org/dc/elements/1.1/}creator"), "作家A")

    def test_rss_uses_original_x_post_time_and_newest_first(self):
        from generate import post_published_at, newest_first
        newer = Post(
            post_id="2123966340323225683", text="新しい投稿", display_name="作家N",
            username="artist_n", url="https://x.com/artist_n/status/2123966340323225683",
            first_seen="2026-11-21T08:40:20+00:00",
        )
        older = Post(
            post_id="2123941217771868334", text="古い投稿", display_name="作家O",
            username="artist_o", url="https://x.com/artist_o/status/2123941217771868334",
            first_seen="2026-11-21T08:40:20+00:00",
        )
        now = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
        self.assertEqual(post_published_at(newer, now).strftime("%Y-%m-%d %H:%M"), "2026-11-21 02:26")
        self.assertEqual(post_published_at(older, now).strftime("%Y-%m-%d %H:%M"), "2026-11-21 00:47")
        self.assertEqual([p.post_id for p in newest_first([older, newer], now)], [newer.post_id, older.post_id])
        root = ET.fromstring(xml_bytes([older, newer], now))
        items = root.findall("./channel/item")
        self.assertEqual([x.findtext("title") for x in items], ["新しい投稿", "古い投稿"])
        self.assertEqual(items[0].findtext("pubDate"), "Sat, 21 Nov 2026 02:26:51 +0000")
        self.assertEqual(items[1].findtext("pubDate"), "Sat, 21 Nov 2026 00:47:01 +0000")

    def test_empty_or_changed_html_fails_safely(self):
        with self.assertRaises(ValueError):
            parse_buzzweet("<html><p>forbidden</p></html>", page_url(1))


if __name__ == "__main__":
    unittest.main()
