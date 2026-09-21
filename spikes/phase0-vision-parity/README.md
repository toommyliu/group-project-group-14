# Phase 0 — vision parity spike

**This directory is a measurement harness, not shipped code.** It is not
under `src/`, it is not listed in `pyproject.toml`, it is not importable as
part of the `opengrader` package, and nothing in `src/opengrader` may import
from here. Per `AI_README.md` §7.9's Phase 0 row, this phase's rule is
"merge nothing" — the only thing Phase 0 produces is a measurement and a
go/no-go read on it. If you are looking at this from inside the shipped
tool, that is a mistake; this is scratch.

## What this measures

`AI_README.md` §7.9 Phase 0's exit criterion, verbatim:

> Prove one real answer crop from `samples/` transcribes at least as well
> through a hosted vision API as through the current Ollama path, and
> **measure** real token counts per crop rather than estimating them.

That single result decides whether Phase 2 (extracting `VisionReader`, the
largest refactor in §7) is worth building at all. If a hosted API reads
handwriting no better than Ollama, §7.4 is not worth building and the
grading-only increment from Q2 becomes the plan. Getting this measurement
wrong in the optimistic direction means building a real refactor for nothing;
getting it wrong in the pessimistic direction means not building something
that would have worked. Both failure modes matter, which is why the rest of
this document is mostly about how the measurement could be dishonest without
anyone intending it to be.

## The ground-truth trap (read this before running anything)

The obvious design for this spike is: read `Response text` out of each
`*.submission.md` in `samples/`, treat it as ground truth, score every
backend against it. **That design is wrong and would produce a fraudulent
result**, for one specific reason:

Every `*.submission.md` in `samples/` already carries a `Read by:` field.
Where it says `Read by: vision-model`, the `Response text` sitting right
next to it **is the current Ollama pipeline's own prior output** — it is
what `core.ocr.ollama_vlm.extract_question_prompt_and_answer` produced when
the sample was generated. Scoring the Ollama backend against that text
would be scoring Ollama against itself. It would report ~100% agreement
regardless of whether the transcription is actually any good, the "Ollama
wins" conclusion would be guaranteed before a single crop was sent anywhere,
and every hosted backend would look worse than Ollama by construction, not
by any property of its transcription. That is exactly backwards from what
Phase 0 exists to find out.

So this harness is built around a different rule, and it is enforced in
code, not just convention:

1. **A gold file is the only reference.** `gold.toml` maps a crop id
   (`<sample>/<submission>/<question>`) to a transcription a **person on the
   team** verified by looking at the crop image, plus who verified it and
   when. `metrics.compute_cer_summary` reads gold text from `gold.toml` and
   nowhere else.
2. **A crop with no gold entry is excluded from scoring, not silently scored
   against the submission markdown.** `gold.toml` ships with every crop
   present but `gold_text = ""` (unfilled). `metrics.build_adjudication_rows`
   treats an empty `gold_text` as "no gold entry" and `compute_cer_summary`
   skips it — it does not fall back to `Response text`.
3. **The primary output is the adjudication table, not a score.** For every
   crop, `run_spike.py` prints every backend's transcription side by side and
   flags disagreement, so a human can read and judge them. This is useful on
   day one with a completely empty `gold.toml` — the CER path is not, which
   is the point: filling in gold is optional for getting *some* signal out of
   a run, but required for a number.
4. **Wherever the submission markdown's `Response text` is shown, it is
   labeled `ollama-incumbent-recorded`.** Never `reference`, never `truth`.
   The adjudication table's incumbent column carries this label explicitly so
   nobody skimming the output mistakes it for gold.

## Filling in `gold.toml`

`gold.toml` is auto-shaped (one stub block per crop `run_spike.py` currently
discovers under `samples/`) but **not auto-filled** — filling it in is a
human task, on purpose, because the entire point of the file is that a
person looked at the image.

1. Run `uv run python spikes/phase0-vision-parity/run_spike.py --backends
   ollama --dry-run` once to see the current crop ids and confirm they match
   `gold.toml`'s keys (it walks `samples/` fresh each run, so a new sample
   added later shows up automatically as an unfilled stub, but you'd need to
   re-add its block to `gold.toml` by hand — see the file's own header
   comment for the exact shape).
2. Open the crop image the key names, e.g. for
   `cs146-quiz1/submission-0001/q2` open
   `samples/cs146-quiz1/student-submissions/submission-0001/responses/q2.png`.
3. Transcribe it yourself, literally — matching the shipped prompt's own
   convention (`[unclear]` for illegible handwriting, `[mark]` for a
   handwritten non-text mark, empty string for a genuinely blank answer).
4. Fill in `gold_text`, `verified_by`, `verified_on` for that block.
5. An entry stays excluded from CER until `gold_text` is non-empty. Note the
   corollary in `gold.toml`'s own header: right now a genuinely blank answer
   and an unfilled stub look identical to the scorer (both are `""`). That is
   an acceptable gap for a four-sample spike, not a design to carry forward.

`gold.toml` ships with a **worked example** under a key
(`example/submission-0000/q1`) that does not match any real crop, so it
documents the schema without asserting a "human verification" that did not
happen — see the file for why, and copy its shape for real entries.

## Backends

Three, behind the shared interface in `backends/base.py`
(`transcribe(image_path, prompt, config) -> CallResult`):

- **`ollama`** (`backends/ollama_backend.py`) — the current shipped path.
  Reuses `opengrader.core.ai_config.resolve()` for host/model resolution and
  `opengrader.core.ocr.ollama_vlm.extract_question_prompt_and_answer` for the
  actual prompt-building logic, rather than reimplementing either. The one
  thing it does differently from the shipped wrapper: it captures the exact
  request the wrapper would have sent (by swapping `ensure_model_available`
  and `ollama_transport.generate_raw` for network-free capture stubs, then
  restoring them) and POSTs that request to `/api/generate` itself, because
  the shipped wrapper (`ollama_transport.generate_raw`) parses out only the
  `response` string and throws away `prompt_eval_count` / `eval_count` — the
  real token counts this spike exists to measure. See the module docstring
  for the exact mechanism.
- **`anthropic-messages`** (`backends/anthropic_backend.py`) — raw
  `urllib.request.Request` to `POST {host}/v1/messages`, image as a base64
  `image` content block, `x-api-key` + `anthropic-version` headers. Reads
  `usage.input_tokens` / `usage.output_tokens` from the response.
- **`openai-chat`** (`backends/openai_backend.py`) — raw `urllib` to
  `POST {host}/chat/completions`, image as an `image_url` content part with a
  `data:image/png;base64,...` URI, `Authorization: Bearer` header. Reads
  `usage.prompt_tokens` / `usage.completion_tokens`.

No vendor SDK is imported anywhere in `backends/` — per `AI_README.md` §7.8
Q4, raw HTTP only. These two cloud modules are a throwaway sketch of what
Phase 3's `core/llm/dialects/{anthropic_messages,openai_chat}.py` will
contain; keeping each small and in its own file is deliberate so that work
can crib from here.

**All three backends are sent the same prompt.** `run_spike.py` captures the
shipped extraction prompt once per run (via `ollama_backend.capture_prompt`,
which uses the same network-free capture trick above) and passes that exact
string to every backend. This is **the actual Ollama-tuned handwriting
extraction prompt** from `ollama_vlm.py` — it was written and iterated
against Ollama's vision model, never against Anthropic's or OpenAI's. If a
hosted backend underperforms, part of that could be prompt mismatch rather
than a real capability gap, and part of any Ollama "win" could be
home-field advantage. See "Threats to validity" below — this is not a
footnote, it's a real confound Phase 0's conclusion should be read against.

## Running it

```
uv run python spikes/phase0-vision-parity/run_spike.py --help
```

Structural dry run — crop discovery, gold loading, table rendering, **zero
HTTP calls to anything**, no key needed:

```
uv run python spikes/phase0-vision-parity/run_spike.py --backends ollama --dry-run
```

A real Ollama-only run (local, needs `ollama serve` running, no cloud
consent flag required):

```
uv run python spikes/phase0-vision-parity/run_spike.py --backends ollama
```

A real cloud run additionally requires `--yes-send-to-cloud`, and reads the
API key from an environment variable **named** on the command line — never
pass a key as an argument:

```
export ANTHROPIC_API_KEY=sk-...
uv run python spikes/phase0-vision-parity/run_spike.py \
  --backends ollama anthropic-messages \
  --anthropic-model claude-3-5-sonnet-20241022 \
  --api-key-env anthropic-messages=ANTHROPIC_API_KEY \
  --input-per-mtok anthropic-messages=3.0 \
  --output-per-mtok anthropic-messages=15.0 \
  --yes-send-to-cloud
```

Without `--yes-send-to-cloud`, selecting any cloud backend prints exactly
what would be sent (crop count, destination host) and **exits 2 without
making a single HTTP call** — for the whole invocation, including any local
`ollama` backend listed in the same run. No vendor price is hardcoded
anywhere in this harness; `--input-per-mtok` / `--output-per-mtok` come from
the command line because the design doc's manifest format puts pricing in
config for exactly the reason that hardcoded prices go stale.

Useful flags: `--limit N` (smoke-test on the first N crops),
`--project-cohort N` (defaults to 141, the figure `AI_README.md` §7.7 L4
uses for a realistic cohort), `--json-out PATH` (defaults to
`spike-results.json` next to this script).

## Reading the output

Three sections print, in this order, plus a JSON file:

1. **Adjudication table** — one row per crop, the incumbent's recorded text
   (labeled `ollama-incumbent-recorded`), every requested backend's
   transcription, the gold text if filled in, and a disagreement flag. Read
   this first. It is the primary result.
2. **CER summary** — only over crops with a filled gold entry, always with
   `n`, and always with the per-crop numbers printed alongside the mean —
   not just the mean, because a mean over `n=4` presented alone invites more
   confidence than it has earned.
3. **Token counts and cost projection** — per-crop `input`/`output` token
   counts as actually reported by each backend's API response (never
   estimated), an average, and a projected cost for a `--project-cohort`-size
   cohort using the rates you supplied.

The JSON report (`spike-results.json` by default) mirrors all three
sections in a stable, diffable shape, so two runs (e.g. before/after a
prompt tweak) can be compared with `git diff` or `jq`.

## Threats to validity — standing, not resolved by this harness

- **n is small.** Four samples, 32 crops total, most from two courses. A
  CER computed from however many of those end up with filled gold entries
  is not a benchmark; it is a smoke test. Treat any single-digit-n mean CER
  as a data point to read alongside the adjudication table, not a number
  that stands on its own — which is why the harness always prints n and the
  per-crop breakdown next to it.
- **The prompt is Ollama-tuned.** See "Backends" above. Sending the literal
  Ollama prompt to Anthropic/OpenAI is the right call for a like-for-like
  comparison of *this specific prompt's* portability, but it is not
  evidence about what either hosted model could do with a prompt written
  for it. A backend that underperforms here might just need its own prompt.
- **The samples are synthetic.** Everything under `samples/` is
  team-generated test data, not real student submissions collected in the
  wild. Handwriting style, image quality, scan artifacts, and answer
  content may not represent what a real cohort looks like at scale.
- **Gold-file authorship is unverified by construction.** Nothing in
  `gold.toml`'s format distinguishes a careful transcription from a rushed
  one, and nothing enforces that `verified_by` is accurate. It is a text
  file a teammate edits; treat it with the same scrutiny as any other
  self-reported data until someone spot-checks a few entries against the
  images directly.
- **A genuinely blank answer and an unfilled gold stub are indistinguishable
  right now** (both are `gold_text = ""`), which slightly understates
  gold-file coverage if any answer crop is legitimately blank. Noted in
  `gold.toml`'s header; not fixed here because it did not block the Phase 0
  question at the harness's current scale.
