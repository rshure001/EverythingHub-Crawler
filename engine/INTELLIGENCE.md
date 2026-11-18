# Hurricane Core — Intelligence Engine

Hurricane Core is the deterministic intelligence layer behind Everything Hub.
It ranks web evidence and unifies image, audio, video, and design assets in a
shared multimodal search model.

## Multimodal intelligence: media_intelligence.py

- Artwork/images: titles, captions, alt text, OCR, tags, dimensions, thumbnails,
  creator and license metadata.
- Audio: titles, descriptions, transcripts, creator, duration, sample rate and
  channel metadata. A transcription provider supplies transcript text.
- Video: transcripts, scene descriptions, tags, duration, frame rate and
  frame count. Replaceable extractors can supply scene/frame metadata.
- Design: extracted layout text, typography, color descriptions, dimensions and
  creator metadata. Original design files are never modified.
- Unified search spans titles, descriptions, transcripts, OCR, scene notes,
  design text and tags; results can be filtered by media type.
- Provenance and rights are retained. Unknown reuse rights remain unknown.

This module normalizes and searches supplied metadata. It does not claim to
perform OCR, speech recognition, visual recognition or video decoding itself.
Those capabilities plug in through replaceable extractors.

## Extraction authorization gate

Before attaching OCR, transcription, video-scene or design extraction output,
the extraction pipeline requires all of the following on the source asset:

- robots_allowed: true from the crawler's current robots check.
- extraction_permission: true from an approved source-policy review.
- source_terms_status: allow_extraction.
- A public HTTP(S) source_terms_url.
- A timezone-aware source_terms_checked_at no more than 90 days old.
- A 64-character SHA-256 digest identifying the reviewed terms snapshot.

This is an attestation validator, not an automated legal interpretation of
source terms. The review process must actually establish that extraction is
permitted before setting the fields. Missing, stale, ambiguous or denied
permission fails closed. A permission to extract does not grant permission to
redistribute media: existing license, attribution and rights fields are never
upgraded by this gate.

## Evidence intelligence: intelligence_core.py

Collects admitted crawler records, scores valid HTTP(S) evidence, connects
independent hosts without treating repetition as proof, and learns bounded
ranking weights only from explicit human feedback.

## Tests

    python -m unittest -v test_search_core test_catalog_search test_intelligence_core test_media_intelligence test_extraction_pipeline

Hurricane Core is not an autonomous truth oracle. Crawled text and media
metadata are untrusted data, never instructions. The core has no network,
filesystem, publishing, or database side effects. Production persistence and
model/extractor services require separate staged integration.

The dedicated database boundary remains eh_quad in the separate Everything
Hub control project. Never use Dog Tag Day Story Wall storage.
