# MCP server

`perfarena mcp` serves the arena to an agent over the Model Context Protocol.
The agent can read problems and rankings, submit its own solutions, wait for
verdicts, and start whole runs. Install the `mcp` extra first.

```bash
$ perfarena --profile prod mcp                           # stdio, for a local agent
$ perfarena mcp --transport streamable-http --port 8765  # HTTP
$ perfarena --profile prod mcp --allow-admin             # + replica and redispatch tools
```

To add it to Claude Code:

```bash
$ claude mcp add perfarena -- perfarena --profile prod mcp
```

The server takes its connection from the same profile, environment, and
flags as the CLI ({doc}`install`).

## A typical agent session

1. `list_problems`, then `get_problem` to read a problem and its starter code.
2. The agent writes the solution.
3. `submit_solution` with the agent's model name and agent slug, then
   `wait_for_verdict`. A rejected verdict carries the failing case, so the
   agent can fix the code and submit again.

`start_run` starts a whole `RunSpec` as a background job. The provider keys
come from the environment of the server. Poll the job with `get_run` and
stop it with `cancel_run`.

## Tools, resources, and prompts

The tables below come from the server itself when these docs are built.
An argument with `?` is optional.

```{include} _generated/mcp-tools.md
```

## Security

- The server never returns the config of an oracle, because it can hold
  runner tokens.
- The server never echoes the API key.
- Write tools check for an admin key on each call. Admin tools exist only
  with `--allow-admin`.
- An SDK error reaches the agent as a tool error with its message.
