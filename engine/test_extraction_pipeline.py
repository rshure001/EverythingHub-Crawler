import unittest
from extraction_pipeline import normalize_extraction, attach_extractions

class ExtractionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.asset = {"type":"video","url":"https://media.example.org/mission.mp4",
                      "title":"Apollo 11 film","usage_rights":"Unknown",
                      "rights_status":"unknown"}
    def record(self, **overrides):
        row={"source_url":"https://media.example.org/mission.mp4",
             "extraction_type":"transcript","text":"Apollo 11 mission control",
             "provider":"local-whisper","model":"small.en","confidence":0.91}
        row.update(overrides); return row
    def test_normalizes_transcript_with_provenance(self):
        result=normalize_extraction(self.record())
        self.assertEqual(result["field"],"transcript")
        self.assertEqual(result["provider"],"local-whisper")
        self.assertTrue(result["content_is_untrusted"])
    def test_builds_text_from_timestamped_segments(self):
        result=normalize_extraction(self.record(text="",segments=[
            {"start_seconds":2,"end_seconds":3,"text":"second"},
            {"start_seconds":0,"end_seconds":1,"text":"first"}]))
        self.assertEqual(result["text"],"first second")
        self.assertEqual(result["segments"][0]["start_seconds"],0)
    def test_rejects_unsupported_type_missing_provenance_and_bad_url(self):
        for row in (self.record(extraction_type="executable"),
                    self.record(provider=""), self.record(source_url="file:///tmp/a")):
            with self.assertRaises(ValueError): normalize_extraction(row)
    def test_skips_invalid_segments_and_clamps_confidence(self):
        result=normalize_extraction(self.record(text="",confidence=4,segments=[
            {"start_seconds":-1,"end_seconds":2,"text":"bad"},
            {"start_seconds":0,"end_seconds":2,"text":"valid"}]))
        self.assertEqual(result["confidence"],1)
        self.assertEqual(len(result["segments"]),1)
    def test_bounds_segment_count_and_text(self):
        with self.assertRaises(ValueError):
            normalize_extraction(self.record(segments=[{"start_seconds":0,"end_seconds":1,"text":"x"}]*501))
    def test_attaches_matching_text_but_not_other_asset_or_rights(self):
        rows=[self.record(),self.record(source_url="https://elsewhere.example/video.mp4",text="wrong")]
        result=attach_extractions(self.asset,rows)
        self.assertEqual(result["transcript"],"Apollo 11 mission control")
        self.assertEqual(result["usage_rights"],"Unknown")
        self.assertEqual(result["rights_status"],"unknown")
        self.assertEqual(len(result["extraction_provenance"]),1)
    def test_rejects_empty_extraction(self):
        with self.assertRaises(ValueError): normalize_extraction(self.record(text=""))

if __name__=="__main__": unittest.main()
