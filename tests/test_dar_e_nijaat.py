import json
import unittest
from unittest.mock import patch

from ary_episode_watcher import APP_DIR, CACHE, catalogue


class DarENijaatCatalogueTests(unittest.TestCase):
    def test_canonical_mapping_has_twenty_sequential_episodes(self):
        source = json.loads((APP_DIR / "dar-e-nijaat-all-m3u8.json").read_text(encoding="utf-8"))
        episodes = source["episodes"]
        self.assertEqual(source["seriesId"], "6a57868b5bf57c474cc00a50")
        self.assertEqual(source["series"], "Dar-E-Nijaat")
        self.assertEqual([item["episode"] for item in episodes], list(range(1, 21)))
        for item in episodes:
            with self.subTest(episode=item["episode"]):
                self.assertRegex(item["id"], r"^[a-f0-9]{24}$")
                self.assertTrue(item["m3u8"].startswith("https://"))
                self.assertTrue(item["m3u8"].endswith(".m3u8"))

    def test_backend_catalogue_uses_canonical_ids_and_streams(self):
        source = json.loads((APP_DIR / "dar-e-nijaat-all-m3u8.json").read_text(encoding="utf-8"))
        # Keep this unit test offline and deterministic; live discovery is
        # covered by the backend's runtime refresh path.
        with patch("ary_episode_watcher.http_text", return_value="<html></html>"), patch(
            "ary_episode_watcher.api_json", return_value={"episode": [], "hasNextPage": False}
        ):
            episodes = catalogue("dar-e-nijaat", force=True)
        # The live catalogue may contain episodes newer than the 20-entry
        # verified baseline. Every baseline entry must remain present.
        self.assertGreaterEqual(len(episodes), 20)
        by_number = {item["number"]: item for item in episodes}
        for item in source["episodes"]:
            with self.subTest(episode=item["episode"]):
                backend_item = by_number[item["episode"]]
                self.assertEqual(backend_item["id"], item["id"])
                self.assertEqual(backend_item["api_id"], item["id"])
                self.assertEqual(backend_item["stream"], item["m3u8"])


    def test_live_ary_api_adds_episode_beyond_twenty(self):
        CACHE.pop('episodes:dar-e-nijaat', None)
        CACHE.pop('dar_e_nijaat_checked_at', None)
        episode_21 = {
            'id': '6aff00000000000000000021',
            'videoEpNumber': 21,
            'videoTitle': 'Dar-E-Nijaat Episode 21',
        }
        def api_response(path, timeout=20):
            if '/api/v2/cdn/pg/' in path and 'page=1' in path:
                return {'episode': [episode_21], 'hasNextPage': False, 'totalPages': 1}
            return {'episode': [], 'hasNextPage': False}
        with patch('ary_episode_watcher.api_json', side_effect=api_response), patch(
            'ary_episode_watcher.http_text', return_value='<html></html>'
        ):
            episodes = catalogue('dar-e-nijaat', force=True)
        by_number = {int(item['number']): item for item in episodes}
        self.assertIn(21, by_number)
        self.assertEqual(by_number[21]['id'], episode_21['id'])
        self.assertEqual(by_number[21]['api_id'], episode_21['id'])

if __name__ == "__main__":
    unittest.main()
