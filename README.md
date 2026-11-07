# ghome-local-api

An asynchronous Python client for the local API of Google Home devices, being extracted from [ha-google-home](https://github.com/leikoilja/ha-google-home).

## Status

This project is under development. The `ghome_local_api` package contains no code yet, so there is no client API to use, and no release has been published to PyPI.

## Scope

The package is planned to contain the client that ha-google-home uses to send requests to Google Home devices on your local network:

- It won't get the local authentication tokens that these requests need. [glocaltokens](https://github.com/leikoilja/glocaltokens) does that.
- Home Assistant logic, such as polling and entity state, stays in ha-google-home.

## Origin

- The project tooling (formatter, linter and type checker settings, GitHub workflows and their configuration) is adapted from glocaltokens at commit [`6b910db`](https://github.com/leikoilja/glocaltokens/tree/6b910db1fe3e2b2d2fc74ffa299e7a1792f5f6c4).
- The client code is to be moved from ha-google-home in a later change.

## License

MIT, see [LICENSE](https://github.com/KapJI/ghome-local-api/blob/main/LICENSE). It keeps Ilja Leiko's copyright notices from ha-google-home (2020) and glocaltokens (2021), the two projects this one builds on.

## Contributing

See [CONTRIBUTING.md](https://github.com/KapJI/ghome-local-api/blob/main/CONTRIBUTING.md).
