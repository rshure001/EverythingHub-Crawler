import unittest
from media_intelligence import normalize_asset, index_assets, search_assets

class MultimodalIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.assets = [
            {"id":"img-1","type":"image","url":"https://media.example.org/launch.jpg",
             "title":"Apollo 11 launch photograph","alt_text":"Saturn V rocket",
             "ocr_text":"NASA Apollo 11","tags":["space","NASA","space"],"width":1920,"height":1080,
             "license":"CC BY 4.0","license_url":"https://creativecommons.org/licenses/by/4.0/",
             "rights_status":"verified","attribution":"NASA"},
            {"id":"audio-1","type":"audio","url":"https://media.example.org/interview.mp3",
             "title":"Apollo mission interview","transcript":"Saturn V carried Apollo 11 to the Moon",
             "duration_seconds":93.4,"sample_rate":48000,"channels":2},
            {"id":"video-1","type":"video","url":"https://media.example.org/mission.mp4",
             "title":"Apollo 11 documentary","scene_descriptions":"Saturn V lifts off",
             "duration_seconds":1200,"frame_rate":29.97,"frame_count":35964},
            {"id":"design-1","type":"design","url":"https://media.example.org/poster.svg",
             "title":"Apollo mission poster","design_text":"Mission typography gold lettering"}
        ]
    def test_all_four_media_types_normalize(self):
        self.assertEqual([x["media_type"] for x in index_assets(self.assets)],["image","audio","video","design"])
    def test_searches_ocr_transcript_scenes_and_design(self):
        self.assertEqual(search_assets("NASA Apollo",self.assets,media_type="image")[0]["asset_id"],"img-1")
        self.assertEqual(search_assets("Saturn V Moon",self.assets,media_type="audio")[0]["asset_id"],"audio-1")
        self.assertEqual(search_assets("Saturn V lifts off",self.assets,media_type="video")[0]["asset_id"],"video-1")
        self.assertEqual(search_assets("gold lettering",self.assets,media_type="design")[0]["asset_id"],"design-1")
    def test_unknown_rights_never_upgraded(self):
        a=normalize_asset({"type":"image","url":"https://example.org/a.jpg"})
        self.assertEqual(a["rights_status"],"unknown")
        self.assertIn("Unknown",a["usage_rights"])
    def test_verified_requires_license_statement(self):
        a=normalize_asset({"type":"image","url":"https://example.org/a.jpg","rights_status":"verified"})
        self.assertEqual(a["rights_status"],"unknown")
    def test_invalid_types_and_urls_rejected(self):
        for row in ({"type":"document","url":"https://example.org/a"},
                    {"type":"image","url":"javascript:alert(1)"},
                    {"type":"image","url":"https://user:pass@example.org/a"}):
            with self.assertRaises(ValueError): normalize_asset(row)
    def test_deduplication_and_invalid_record_skip(self):
        rows=self.assets+[dict(self.assets[0],url="https://media.example.org/launch.jpg#copy"),
                          {"type":"image","url":"file:///tmp/a.jpg"}]
        self.assertEqual(len(index_assets(rows)),4)
    def test_nonfinite_values_and_tags_are_bounded(self):
        a=normalize_asset({"type":"video","url":"https://example.org/v.mp4",
                           "duration_seconds":float("inf"),"frame_rate":-1,"tags":["x"*500]})
        self.assertIsNone(a["duration_seconds"]); self.assertEqual(a["frame_rate"],0)
        self.assertLessEqual(len(a["tags"][0]),80)
    def test_filter_is_validated(self):
        with self.assertRaises(ValueError): search_assets("apollo",self.assets,media_type="executable")

if __name__=="__main__": unittest.main()
