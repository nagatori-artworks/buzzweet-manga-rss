import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import xml.etree.ElementTree as ET

import generate


def sample_page(ids):
    return '<html><body>' + ''.join(f'''
    <div class="tweet-card">
      <a href="https://x.com/ac{n[-3:]}">作家{n}</a><span>@ac{n[-3:]}</span>
      <div>漫画の内容{n}</div><div>11月21日・12時00分・10万件の表示</div>
      <a href="https://x.com/intent/retweet?tweet_id={n}">RT</a>
      <a href="https://x.com/intent/like?tweet_id={n}">Like</a>
      <a href="https://x.com/ac{n[-3:]}/status/{n}">open</a>
    </div>''' for n in ids) + '</body></html>'


class FakeResponse:
    def __init__(self, body, json_obj=None):
        self.text = body
        self._obj = json_obj
    def raise_for_status(self):
        return None
    def json(self):
        return self._obj


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
    def get(self, url, timeout=30):
        self.calls.append(url)
        if 'api.fxtwitter' in url:
            return FakeResponse('', {'status': {'media': {'photos': [{'url': 'https://pbs.twimg.com/media/test.png'}]}}})
        return FakeResponse(self.pages[url])


class EndToEndTests(unittest.TestCase):
    def test_first_run_then_second_run_no_duplicate(self):
        # Use real 19-digit IDs; API/link parser rejects shorter fake numbers.
        a = ['2085923960588234759', '2085923960588234758', '2085923960588234757']
        b = ['2085923960588234756', '2085923960588234755', '2085923960588234754']
        c = ['2085923960588234753', '2085923960588234752', '2085923960588234751']
        with tempfile.TemporaryDirectory() as d:
            work = Path(d)
            with (patch.object(generate, 'ROOT', work),
                  patch.object(generate, 'SITE', work/'site'),
                  patch.object(generate, 'RSS_FILE', work/'site'/'manga.xml'),
                  patch.object(generate, 'STATE_FILE', work/'state.json'),
                  patch.object(generate, 'STATUS_FILE', work/'site'/'status.json'),
                  patch.object(generate.time, 'sleep')):
                pages = {generate.page_url(i): sample_page(items) for i, items in enumerate([a, b, c], 1)}
                s = FakeSession(pages)
                with patch.object(generate, 'http_session', return_value=s):
                    self.assertEqual(generate.main(), 0)
                rss_path = work/'site'/'manga.xml'
                parsed = ET.parse(rss_path)
                items = parsed.findall('./channel/item')
                self.assertEqual(len(items), 9)
                self.assertEqual(len([u for u in s.calls if 'buzzweet' in u]), 3)
                self.assertEqual(len([u for u in s.calls if 'api.fxtwitter' in u]), 9)
                self.assertTrue(items[0].findtext('link').endswith(a[0]))
                status = json.loads((work/'site'/'status.json').read_text())
                self.assertEqual(status['new_posts_with_image'], 9)
                self.assertEqual(status['pages_scanned'], 3)
                state = json.loads((work/'state.json').read_text())
                self.assertEqual(state['anchor_id'], a[0])
                # Next run same day: the marker is on the first page;
                # no need to fetch pages 2-3 or refetch all images.
                s2 = FakeSession(pages)
                with patch.object(generate, 'http_session', return_value=s2):
                    self.assertEqual(generate.main(), 0)
                self.assertEqual(len([u for u in s2.calls if 'buzzweet' in u]), 1)
                self.assertEqual(len([u for u in s2.calls if 'api.fxtwitter' in u]), 0)
                self.assertEqual(len(ET.parse(rss_path).findall('./channel/item')), 9)


if __name__ == '__main__':
    unittest.main()
