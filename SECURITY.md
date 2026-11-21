# Security Policy

BlockHost handles a Google login token and an encryption key for a group's world, so security reports matter.

## Reporting a vulnerability
Please **don't open a public issue**. Use **Security → Report a vulnerability** (GitHub private advisory) on this repository.
Include steps to reproduce and the affected version. You'll get a reply within 7 days.

## Design summary
- Google Drive scope `drive.file`: the app can access only files it created.
- World archives: AES-256-GCM, streamed in 1 MiB chunks, with truncation and tamper detection.
- Group file: scrypt (N=2^15) + AES-256-GCM, keyed by the group password.
- Local cache: Windows DPAPI / macOS Keychain.
- Server process is bound to the launcher's lifetime (job object / watchdog).

## Known limitations
- Anyone holding the group file **and** password can host and modify the world. To revoke someone, create a new group.
- With online mode off, the Minecraft server does not verify player identities. Use it only on trusted networks.
- Builds are not code-signed, so Windows SmartScreen and macOS Gatekeeper show a first-run warning.
