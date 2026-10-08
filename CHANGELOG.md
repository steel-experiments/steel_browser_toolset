# Changelog

All notable changes are in this file. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `zoom` makes Chrome draw the region again at the zoom scale, so small text is sharp. Before, it enlarged a normal
  screenshot of the region, and the text became blurred. Pillow is no longer a runtime dependency.

## [0.1.0] - 2026-10-08

### Added

- `SteelBrowser`, a driver for Claude's browser toolset (`browser_toolset_20260801`) on Steel cloud browsers, with all
  31 members.
- `read_page` and `find` from the Chrome accessibility tree, with stable `ref_N` targets.
- A URL policy check on every browser request, not only on `navigate`, and `allowed_hosts_policy`.
- `file_upload` through the Steel Files API, for local paths and for Anthropic Files API documents.
- Download, dialog and popup reports in the browser state.
- Unit tests, and live tests against a real Steel session.
