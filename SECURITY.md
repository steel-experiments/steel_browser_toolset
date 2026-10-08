# Security policy

## Report a vulnerability

Report privately through GitHub security advisories. In this repository, open the **Security** tab and choose
**Report a vulnerability**. Do not open a public issue for a security report, and do not post API keys or session
URLs anywhere.

Include the commit you tested, the steps that reproduce the problem, and the impact you see.

## What counts as a security issue

The driver stands between a model that reads untrusted pages and a real browser. Report these first:

- **URL policy bypass.** The browser opens or requests an address that the configured `url_policy` refuses.
- **Upload path escape.** `file_upload` sends a local file that the file policy does not allow.
- **Leaks to the model.** Local paths, API keys or session connection URLs in text that the model reads.
- **Session leaks.** A session that the driver created stays open after `close`.

Steel session isolation, egress and recordings are part of the Steel platform. Report those to Steel at
[steel.dev](https://steel.dev).

## Supported versions

Only the latest release gets fixes.
