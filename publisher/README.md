# EverythingHub Publisher

A separate, free-tier static release pipeline for EverythingHub assets. It never edits the crawler index or search API.

## Release contract
- Only files under `site/` are publishable.
- A release is accepted only when `site/release-manifest.json` lists each file, byte size, and SHA-256 digest.
- The workflow validates the manifest, builds an immutable artifact, then deploys it to GitHub Pages.
- GitHub Pages retains prior deployments for rollback; a rollback is a deliberate redeploy of a prior artifact.
- No credentials, billing actions, third-party paid services, or production search changes are part of this publisher.

## Local validation
```bash
python publisher/publish.py --check
```

## GitHub setup
The repository must have GitHub Pages enabled with **GitHub Actions** as the build source. The workflow uses the standard Pages deployment environment and requires no external API token.
