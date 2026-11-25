# Everything Hub Windows build bridge

Manual build jobs execute on a Windows machine controlled by the project. This is separate from the scheduled public crawler.

## One-time setup
Requirements: Windows x64, Git, Python 3.12+, and GitHub CLI (gh).

1. Run `gh auth login` using an account with admin access to `rshure001/EverythingHub-Crawler`.
2. From PowerShell run `powershell -ExecutionPolicy Bypass -File .\tools\windows-bridge\install.ps1`.
3. Start the runner in the foreground:
   `cd "$env:USERPROFILE\EverythingHub-Windows-Runner"`
   `.un.cmd`

The installer gets a short-lived registration token directly through GitHub CLI. It does not store that token in the repository, install a Windows service, or change firewall, DNS, database, or production settings.

## Dispatch
In the repository Actions tab, select **Everything Hub Windows bridge**, choose **Run workflow**, then choose:
- `validate`: compile crawler modules, run crawler tests, and validate the public index.
- `build-search-index`: rebuild and validate public search shards from canonical repository data.

A seven-day artifact contains a report and, for a build, a ZIP of `public-search/`. This initial bridge does not commit or publish output; inspect the artifact before a separate publish step.

## Security
- Requires the explicit `everythinghub` runner label.
- Manual-only; no arbitrary push or pull-request triggers.
- Checkout credentials are not persisted.
- The runner executes repository code. Restrict repository write access and stop the runner when local execution is not wanted.
