# Security and safety reporting

FART controls real lighting equipment and network protocols, and listens for Art-Net/sACN, OSC, and PSN input on the local network — including, with [live console control](README.md#live-console-control-optional) configured, a full DMX frame from a console that can change which fixtures are lit or which marker they follow. Please avoid publishing a report that could create an immediate show-safety risk before maintainers have had a chance to investigate.

Use the repository's [private vulnerability reporting page](https://github.com/bfulham/fart/security/advisories/new) when available. Include:

- FART version
- Operating system
- Input and output modes
- Reproduction steps
- Expected and observed behaviour
- Whether the issue can unexpectedly enable intensity or move fixtures

This project is experimental and provided without warranty. It is not a safety-rated control system.
