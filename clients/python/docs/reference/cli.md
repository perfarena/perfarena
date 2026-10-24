# Command line

The `perfarena` command calls the SDK and nothing else. The help below comes
from the parser when these docs are built.

The connection options (`--profile`, `--base-url`, `--api-key-env`) go
before the command: `perfarena --profile prod run spec.yaml`. The `run` and
`spec show` commands have one flag per spec field ({doc}`spec`). A flag
overrides the value of the spec file.

```{include} ../_generated/cli-help.md
```
