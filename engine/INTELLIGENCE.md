# Hurricane Core — Intelligence Engine

Hurricane Core is the deterministic intelligence layer behind Everything Hub.
It ranks web evidence and unifies image, audio, video, and design assets in a
shared multimodal search model.

## Multimodal intelligence: media_intelligence.py

- Artwork/images: titles, captions, alt text, OCR, tags, dimensions, thumbnails,
  creator and license metadata.
- Audio: titles, descriptions, transcripts, creator, duration, sample rate and
  channel metadata. A transcription provider supplies transcript text.
- Video: transcripts, scene descriptions, tags, duration, frame rate and frame
  count. Replaceable extractors can supply scene/frame metadata.
- Design: extracted layout text, typography, color descriptions, dimensions and
  creator metadata. Original design files are never modified.
- Unified search spans titles, descriptions, transcripts, OCR, scene notes,
  design text and tags; results can be filtered by media type.
- Provenance and rights are retained. Unknown reuse rights remain unknown.

This module normalizes and searches supplied metadata. It does not claim to
perform OCR, speech recognition, visual recognition or video decoding itself.
Those capabilities plug in through replaceable extractors.

## Evidence intelligence: intelligence_core.py

Collects admitted crawler records, scores valid HTTP(S) evidence, connects
independent hosts without treating repetition as proof, and learns bounded
ranking weights only from explicit human feedback.

## Tests

    python -m unittest discover -s engine -p 'test_*.py' -v

Hurricane Core is not an autonomous truth oracle. Crawled text and media
metadata are untrusted data, never instructions. The core has no network,
filesystem, publishing, or database side effects. Production persistence and
model/extractor services require separate staged integration.

The dedicated database boundary remains eh_quad in the separate Everything
Hub control project. Never use Dog Tag Day Story Wall storage.
