# Contribution guidelines

This project is in early development: the package has no client code yet. Before you start a larger change, open an [issue](../../issues) to discuss it.

## Pull requests

1. Fork the repository and create your branch from `main`.
2. If you change something, update the documentation.
3. Make sure your change passes the checks (see below).
4. Open a pull request.

## Setting up

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and run `uv sync` in the repository root.
It creates a virtual environment with all required packages. ghome-local-api needs Python 3.12 or newer;
uv downloads a matching Python if you don't have one.

## Checks

The checks run with [prek](https://prek.j178.dev/), a faster drop-in replacement for `pre-commit`, using the settings included in the repository.
They format the code with [ruff](https://docs.astral.sh/ruff/) and [dprint](https://dprint.dev/), check it with ruff, mypy and pylint,
look for typos with codespell, and check the GitHub workflow and configuration files with check-jsonschema and [zizmor](https://docs.zizmor.sh/).

Activate the `prek` git hook:

```console
$ uvx prek install
```

Now the checks will run every time you commit.

You can also run the checks on all repository files manually with this command:

```console
$ uvx prek run --all-files
```

There are no tests yet. On every pull request, CI runs the checks on Python 3.12 and 3.14, builds the package, installs the wheel
and checks that it contains the `py.typed` typing marker and the license's copyright notices, and that it depends only on aiohttp.

## Labels and releases

Release notes are drafted automatically from merged pull requests. Labels decide where each pull request goes
in the notes and which version comes next:

- `breaking`: a major version.
- `feature`: a minor version.
- Anything else: a patch version.
- `skip-release`: leaves the pull request out of the release notes.

The next release uses the biggest of these among the pull requests merged since the last release.

Pull requests that only change dependencies, documentation, tests or GitHub files are labelled automatically.
Pull requests that change Python code need a label from a maintainer, such as `bug`, `feature` or `refactoring`.

Nothing is uploaded to PyPI yet: publishing a GitHub release only builds the package.

## Any contributions you make will be under the MIT Software License

In short, when you submit code changes, your submissions are understood to be under the same [MIT License](http://choosealicense.com/licenses/mit/) that covers the project. Feel free to contact the maintainers if that's a concern.
