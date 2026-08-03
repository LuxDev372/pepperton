# Security notes

## The server is unauthenticated by design

Pepperton's observatory (default port 8811) is built for localhost and
trusted home LANs. There is no login, and the HTTP API can:

- pause/resume the simulation (`POST /api/control/pause`)
- fire Director chaos events (`POST /api/chaos`)
- take control of any villager (`POST /api/possess/{name}`)
- read every villager's memories and prompts (`GET /api/agent/{name}`)

**Do not expose this server to the internet.** No port-forwarding, no
"just for the weekend" tunnels. Anyone who can reach the port owns
your town. If you must reach it remotely, put it behind a VPN
(WireGuard/Tailscale) or an authenticating reverse proxy — and even
then, remember the API was never designed to face hostile traffic.

## Outbound connections

The sim makes exactly two kinds of outbound requests, both readable in
the source: RSS fetches for the radio (`sim/radio.py`, feeds listed in
`config.py`) and calls to the Ollama hosts you configure. There is no
telemetry, no update check, and nothing that phones anywhere else.

## Scope line

Pepperton is a closed simulation. It has no integrations with real
social platforms, messaging services, or real-world identities, and
contributions adding them will be declined regardless of intent.

## Reporting

Found something genuinely wrong (a path from the API to the host
system, for example)? Open an issue marked SECURITY, or contact the
maintainer privately if the repo lists a contact.
