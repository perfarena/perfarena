# Install and connect

## Install

The package needs Python 3.11 or later. Install it from the repository:

```bash
$ pip install -e clients/python            # SDK and CLI
$ pip install -e "clients/python[mcp]"     # + MCP server
$ pip install -e "clients/python[sign]"    # + Ed25519 trace signing
$ pip install -e "clients/python[tokens]"  # + tiktoken code token counts (else ~4 chars/token)
$ pip install -e "clients/python[docs]"    # + this documentation
$ pip install -e "clients/python[dev]"     # + tests
```

## Connect to an arena

{py:class}`~perfarena.config.Settings` holds how to reach one arena: the base
URL, the API key, the timeout, the request cap, and the retry count.
`Settings.resolve()` reads them from these sources, lowest priority first:

1. the defaults (`http://localhost:8000`, no key);
2. a profile;
3. the environment: `PERFARENA_BASE_URL` and `PERFARENA_API_KEY`, or the
   scripts' names `ARENA_BASE_URL` and `ARENA_API_KEY`;
4. explicit keyword arguments.

A profile that you name explicitly wins over the environment. Thus a local
key in your shell is never sent to the host of another profile.

### Profiles

A profile file holds named connections. It never holds a key, only where to
read it (`api_key_env` or `api_key_file`):

```toml
# ~/.config/perfarena/profiles.toml   (or the path in $PERFARENA_PROFILES)
[default]
base_url = "http://localhost:8000"
api_key_env = "ARENA_API_KEY"

[prod]
base_url = "https://perfarena.ngrok.app"
api_key_file = "~/.perfarena_admin_key"
max_concurrency = 2
```

Choose a profile in code, with `--profile` on the CLI, or with
`PERFARENA_PROFILE`:

```python
from perfarena import Arena

arena = Arena.from_profile("prod")
arena.problems.get("two-sum")["title"]
```

```bash
$ perfarena --profile prod competitions
```

### Request limits and retries

The client caps parallel requests per host: 2 for a public host and 8 for
`localhost`. Set `max_concurrency` to change the cap. The client retries
only idempotent calls (GET, PUT, DELETE), with back-off, after a connection
error or a transient status (408, 425, 429, 5xx). It never retries a POST,
so a retry cannot create a duplicate submission.

## API keys

Read calls need no key. Calls that write (create a submission, submit an
attempt, start a run) need an **admin** API key, as the REST API does. The
admin tools of the MCP server also need `--allow-admin`.

## Errors

Every SDK error is a {py:class}`~perfarena.errors.PerfArenaError`. Nothing
below the CLI raises `SystemExit`, so a caller can tell a transient failure
from a permanent one without reading the message.

| Error | Raised when | `transient` |
|---|---|---|
| `ArenaHTTPError` | the arena API answers with an error status (`.status`, `.body`) | for 408, 425, 429, and 5xx |
| `ArenaConnectionError` | the arena API cannot be reached | always |
| `ProviderError` | an LLM provider call fails (`.timeout` for a generation timeout) | for rate limits, gateways, and dropped connections |
| `SpecError` | a `RunSpec` is not valid or does not fit the server | never |
| `PolicyMismatch` | an oracle's replica policy differs from the spec | never |
