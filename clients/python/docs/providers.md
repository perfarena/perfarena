# LLM providers

A provider sends one prompt pair (system, user) to a model and returns the
raw answer, the reasoning, and the provider's metadata. It does transport
only. The SDK builds the prompt and extracts the code once, above the
provider, so every provider gets the same prompt and the same extraction.

## Built-in providers

| Name | Endpoint (default) | API key |
|---|---|---|
| `ollama` | `$OLLAMA_URL`, else `http://localhost:11434` | none |
| `openai` | `$OPENAI_BASE_URL`, else `https://api.openai.com/v1` | `$OPENAI_API_KEY` |
| `anthropic` | `$ANTHROPIC_BASE_URL`, else `https://api.anthropic.com` | `$ANTHROPIC_API_KEY` |
| `fake` | none (no network) | none |

Set `endpoint` and `api_key_env` on a model to use another host or key:

```yaml
model:
  name: qwen/qwen3-coder
  provider: openai                 # any OpenAI-compatible endpoint
  endpoint: https://openrouter.ai/api/v1
  api_key_env: OPENROUTER_API_KEY  # the NAME of the variable, never the key
```

The `openai` provider sends reasoning models (the gpt-5 family and the
o-series) to `/responses`, and every other model to `/chat/completions`.

The `fake` provider answers every request with a placeholder, which the
language check then rejects. Use it to try a spec without a model:
`perfarena run spec.yaml --provider fake`.

## Middleware

The SDK wraps every provider in the same two decorators
({py:func}`~perfarena.llm.middleware.standard_chain`):

`RetryOnConnectionError`
: Retries a call whose connection failed (refused, reset, dropped), 5
  times, with back-off from 5 s up to 60 s. It does not retry an HTTP error
  (the server answered) or a timeout (a timed-out generation is a result,
  `generation_timeout`).

`HardTimeout`
: A wall-clock cap on the whole call (`inference_timeout_s`). A socket
  timeout alone is not enough, because an endpoint that streams keep-alive
  whitespace resets it forever.

## Add a provider

A provider subclasses {py:class}`~perfarena.llm.base.Provider` and
implements `complete`:

```python
import time

from perfarena.llm import Completion, GenerationRequest, Provider, register_provider


class EchoProvider(Provider):
    name = "echo"
    local = True                   # local inference costs nothing

    def complete(self, req: GenerationRequest) -> Completion:
        t0 = time.perf_counter()
        answer = my_backend(req.model, req.system_prompt, req.user_prompt, **req.params)
        return Completion(content=answer, reasoning=None,
                          wall_clock_ms=int((time.perf_counter() - t0) * 1000))


@register_provider("echo")
def make(model_spec) -> Provider:
    return EchoProvider()
```

Raise {py:class}`~perfarena.errors.ProviderError` on failure. Set
`timeout=True` when the model did not answer in time, and `transient=True`
for a rate limit or a gateway error. Raise
{py:class}`~perfarena.llm.base.ProviderConnectionError` when the connection
failed, so that the middleware retries it.

`register_provider` works inside one process. To make the provider available
from another package, also for the CLI and the MCP server, declare an entry
point in the group `perfarena.providers`. The entry point names the factory:

```toml
# pyproject.toml of your package
[project.entry-points."perfarena.providers"]
echo = "mypkg.provider:make"
```

## Replace providers in a run

`Arena.run` takes a `provider_factory`, a function from a
{py:class}`~perfarena.spec.ModelSpec` to a provider. Tests use it to run a
whole spec without a model:

```python
from perfarena.llm import FakeProvider

answers = ["```python\nclass Solution: ...\n```"]
arena.run(spec, provider_factory=lambda model: FakeProvider(answers))
```

A custom factory replaces the default one, so its providers get no
middleware. Wrap them with `standard_chain(provider, timeout_s=...)` to keep
the retries and the timeout.
