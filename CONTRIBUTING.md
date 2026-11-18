# Contributing to Aqara Camera G100 Integration

First off, thanks for taking the time to contribute! 🎉

## Code of Conduct

This project and everyone participating in it is governed by the [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code. Please report unacceptable behavior to [giovannilamarmora.working@gmail.com](mailto:giovannilamarmora.working@gmail.com).

## How Can I Contribute?

### Reporting Bugs

* **Use the Issue Search** — check if the issue has already been reported.
* **Check if the issue has been fixed** — try the latest commit on `main`.
* **Include G100 Diagnostics** — press **Scansione Diagnostica G100** or download Home Assistant diagnostics (credentials are redacted automatically) so we can verify firmware RIDs and traits.

### Suggesting Enhancements

Open an issue describing the camera feature, RID/trait behavior on your G100 firmware, and expected Home Assistant entities.

### Pull Requests

1. Follow the existing module layout under `custom_components/ha_aqara`.
2. Keep `strings.json`, `translations/en.json`, and `translations/it.json` in 100% key parity.
3. Ensure both `hacs/action` and `hassfest` checks pass in CI.

## Styleguides

* Keep Python code formatted cleanly (Black / Ruff compatible, 4-space indentation, type annotations).
* Keep `manifest.json` keys strictly sorted alphabetically after `domain` and `name`.
