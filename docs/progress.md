# Step-by-step Progress

This page records what each completed step added and how to inspect or execute
it. Run all commands from the repository root unless a step says otherwise.

Each future step will document:

- the files added or changed;
- the capability introduced;
- the exact command to run;
- the expected result;
- whether it performs network access, writes runtime data, or uses AI tokens.

## Step 1: Conversion contract and architecture

Step 1 added project documentation. It has no executable code.

Open these files in an editor:

- `README.md`
- `docs/conversion-contract.md`
- `docs/architecture.md`

Optional terminal inspection:

```bash
sed -n '1,220p' docs/conversion-contract.md
sed -n '1,260p' docs/architecture.md
```

Expected result: the terminal prints the correctness contract and planned
pipeline. There is no network access, file generation, or AI-token usage.

## Step 2: Python scaffold and catalog models

Step 2 added:

- the root `pyproject.toml` package definition;
- `PackageRecord` and `PackageCatalog` data models;
- JSON Schemas for their serialized forms;
- model unit tests;
- an executable model demonstration.

### Run the unit tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected result: six tests run and finish with `OK`. They demonstrate metadata
normalization, stable package IDs, HTTPS/ZIP validation, duplicate rejection,
serialization, and basic schema checks.

### Run the demonstration

```bash
PYTHONPATH=src python3 examples/step_02_models.py
```

Expected result: the script prints one normalized package as JSON, including
its stable catalog ID. It then shows an invalid HTTP/non-ZIP URL being rejected.

### Check the JSON Schema files

```bash
python3 -m json.tool schemas/package-record.schema.json >/dev/null
python3 -m json.tool schemas/package-catalog.schema.json >/dev/null
```

Expected result: both commands exit silently with status zero.

All Step 2 commands are local. They make no HTTP requests, require no API key,
and consume no AI-model tokens. Python may create ignored `__pycache__`
directories while executing the tests or demonstration.

## Step 3: Load and query the real metadata catalog

Step 3 added a strict local JSON loader and search helpers. It validates the
top-level category structure and every package row, produces deterministic
`PackageRecord` ordering, and includes the source location in validation
errors.

### Run the catalog demonstration

```bash
PYTHONPATH=src python3 examples/step_03_catalog.py
```

Expected result: the script reports 1,945 packages in 102 categories, displays
the five largest categories, and finds `IPS Display 2 Click`.

Supply another partial package name as the optional positional argument:

```bash
PYTHONPATH=src python3 examples/step_03_catalog.py "air quality"
```

When debugging in VS Code, open `examples/step_03_catalog.py`, press `F5`, and
leave the optional argument empty to use `IPS Display 2`.

### Run all tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected result: thirteen tests run and finish with `OK`. Three integration
tests read the real metadata file; the remaining tests exercise valid and
invalid in-memory data.

Step 3 only reads `metadata_clicks_c.json`. It does not perform HTTP requests,
write conversion output, require an API key, or consume AI-model tokens.

## Step 4: Download and content-address Click archives

Step 4 added a standard-library HTTP downloader. It streams one selected ZIP
to a temporary file, enforces a 64 MiB default limit, rejects non-HTTPS
redirects, verifies that the result is a ZIP, calculates SHA-256, and atomically
moves valid content into `.cache/archives/<sha256>.zip`.

An index under `.cache/index` connects the source package to its content hash.
A repeated request verifies the cached file and avoids HTTP unless `--force` is
used.

### Preview without downloading

```bash
PYTHONPATH=src python3 examples/step_04_download.py
```

Expected result: the script selects `IPS Display 2 Click`, prints its URL and
cache destination, and states that no network request was made. This is also
what happens when the file is launched with `F5` without arguments.

### Perform the real HTTP download

```bash
PYTHONPATH=src python3 examples/step_04_download.py --yes
```

Expected result: the archive is saved below `.cache/archives`, and its size and
SHA-256 are printed. Running the same command again should report `validated
cache` instead of `network download`.

To deliberately refresh the cached package:

```bash
PYTHONPATH=src python3 examples/step_04_download.py --yes --force
```

These commands use ordinary HTTPS only. They never call an AI model, require an
OpenAI API key, or consume model tokens. The `.cache` directory is ignored by
Git and may be deleted when cached downloads are no longer needed.

### Run all tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected result: twenty-two tests run and finish with `OK`. Downloader tests use
mock HTTP responses and temporary directories, so the test suite itself remains
offline and does not modify `.cache`.

## Step 5: Safely inspect and classify Click archives

Step 5 added extraction-free ZIP inspection and the versioned `ArchiveFile` and
`PackageInventory` records. Before reading member contents, the inspector
rejects unsafe relative paths, backslash paths, drive prefixes, symbolic links,
special files, encrypted entries, case-insensitive path collisions, excessive
file counts, excessive expanded sizes, and suspicious compression ratios.

Every regular file receives a logical role and SHA-256. Exact-content
duplicates point to the first deterministic path instead of being treated as
new conversion inputs. Generated Doxygen output is labeled
`generated_documentation`, allowing later context-building stages to exclude
it without losing provenance.

### Inspect an already cached package

First download the package if Step 4 has not been run:

```bash
PYTHONPATH=src python3 examples/step_04_download.py --yes
```

Then inspect it:

```bash
PYTHONPATH=src python3 examples/step_05_inspect.py
```

The inspection command performs no network access and writes no files. For the
currently cached `IPS Display 2 Click` archive, it reports 121 regular files,
2,455,328 expanded bytes, 16 duplicate files, and counts for each role. It does
not extract archive members.

If the package is not cached, one command can explicitly permit the ordinary
HTTPS download before inspection:

```bash
PYTHONPATH=src python3 examples/step_05_inspect.py --download
```

To view the complete versioned inventory:

```bash
PYTHONPATH=src python3 examples/step_05_inspect.py --json
```

When debugging in VS Code, open `examples/step_05_inspect.py` and press `F5`.
With no arguments it only reads an existing validated cache entry. Add
`--download` to the launch configuration's `args` only when network access is
intended.

### Run all tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected result: thirty tests run and finish with `OK`. Archive security tests
create synthetic ZIPs in temporary directories and remain offline.

Step 5 itself has no model-client dependency, requires no OpenAI API key, and
consumes no AI-model tokens. Only the explicitly permitted `--download` path
can make a normal HTTPS request or write new files under `.cache`.

## Step 6: Interpret manifests and select canonical sources

Step 6 added manifest-driven source selection and the versioned `SourceBundle`
record. It strictly parses the package `manifest.json` and each selected
`manifest.exm`, resolves their paths against the inspected inventory, and reads
the `add_library` and `add_executable` source declarations from the applicable
`CMakeLists.txt` files.

The CMake handling is intentionally a bounded source-list reader, not a full
CMake evaluator. Conditional compile definitions and dependency resolution
remain inputs for later parsing and SDK-resolution stages; the complete build
metadata text is retained so none of that information is lost.

Selected UTF-8 text is limited to 2 MiB per file and 8 MiB in total by default.
Each file is rechecked against its inventory size and SHA-256, and the complete
archive checksum is verified again to guard against changes between stages.
Large `*_resource.c` files remain resource metadata at this point instead of
being loaded into future model context.

### Select sources from the cached package

```bash
PYTHONPATH=src python3 examples/step_06_sources.py
```

For the current `IPS Display 2 Click` package, the command selects ten UTF-8
files totaling 78,728 bytes: the package and example manifests, three build
metadata files, two driver headers, one driver implementation, one canonical
example, and the README. A 524,719-byte generated C resource is retained by
metadata only. The copied example-library tree and 102 generated documentation
files are not loaded.

Print the complete source bundle, including selected text contents:

```bash
PYTHONPATH=src python3 examples/step_06_sources.py --json
```

If a selected package is not cached, explicitly permit its ordinary HTTPS
download with:

```bash
PYTHONPATH=src python3 examples/step_06_sources.py "PACKAGE NAME" --download
```

When debugging in VS Code, open `examples/step_06_sources.py` and press `F5`.
No arguments are needed for the already cached IPS Display 2 package.

### Run all tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Expected result: thirty-five tests run and finish with `OK`. Step 6 tests cover
manifest and CMake selection, duplicate exclusion, resource deferral, UTF-8
validation, total text limits, unsafe manifest paths, and archive-change
detection. They create temporary archives and remain offline.

Source selection reads the existing cache without extracting or writing files.
It has no model-client dependency, requires no OpenAI API key, and consumes no
AI-model tokens. Only `--download` can perform normal HTTPS and cache writes.

## Step 7: Parse C into `ClickPackageIR`

Step 7 added the first non-standard runtime dependencies and pins them in
`pyproject.toml`:

- `tree-sitter==0.26.0`
- `tree-sitter-c==0.24.2`

Install their binary wheels in the project virtual environment:

```bash
.venv/bin/python -m pip install --only-binary=:all: \
    tree-sitter==0.26.0 tree-sitter-c==0.24.2
```

This installation command accesses the Python package index and writes only to
`.venv`. It does not require an OpenAI key or call an AI model.

The parser converts the canonical driver headers, implementations, and example
sources from `SourceBundle` into a versioned `ClickPackageIR`. For every parsed
file it records the exact parser/grammar versions and source SHA-256, then
extracts:

- includes and whether they use system or local syntax;
- object-like and function-like macros with complete replacements;
- function declarations and definitions, parameters, bodies, storage classes,
  documentation, and called expressions;
- structs, unions, fields, enums, members, and typedefs;
- file-scope variables and initializers;
- preprocessor conditions and the condition chain applying to each symbol;
- one-based line/column positions and zero-based byte ranges;
- recoverable Tree-sitter warnings and syntax errors.

### Parse the cached IPS Display 2 package

```bash
PYTHONPATH=src .venv/bin/python examples/step_07_parse_c.py
```

The current package produces four parsed C files and 27 function definitions:
24 driver functions and the example's `application_init`, `application_task`,
and `main`. The summary also prints 21 potential external calls that will be
inputs to SDK resolution.

The header currently reports one recoverable missing-`#endif` grammar warning.
The parser retains the other extracted facts and exposes the exact warning
location rather than treating the complete header as unusable.

Print the complete versioned IR, including exact function bodies and source
slices:

```bash
PYTHONPATH=src .venv/bin/python examples/step_07_parse_c.py --json
```

Process another package and explicitly permit ordinary HTTPS if it is not yet
cached:

```bash
PYTHONPATH=src .venv/bin/python examples/step_07_parse_c.py \
    "PACKAGE NAME" --download
```

When debugging in VS Code, open `examples/step_07_parse_c.py` and press `F5`.
No arguments are required for the cached IPS Display 2 package.

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: thirty-nine tests run and finish with `OK`. Parser tests cover
macros, documentation, structs, enums, typedefs, globals, prototypes,
definitions, call extraction, source reconstruction from byte ranges,
preprocessor context, function-pointer discrimination, and recoverable syntax
diagnostics.

After dependencies are installed, parsing reads only local source text and the
validated cache. It performs no network requests, extracts or writes no package
files, requires no OpenAI key, and consumes no AI-model tokens. As in earlier
examples, only the explicit `--download` option permits normal HTTPS/cache
writes.

## Step 8: Index and resolve the C/Rust SDK pair

Step 8 adds two versioned deterministic records:

- `SdkMappingDatabase` inventories the public C and Rust SDK functions and
  records direct, adapted, or unsupported mappings.
- `TranslationPlan` classifies every call from `ClickPackageIR`, records its
  callers and evidence, and calculates the minimal Rust crate set.

The C SDK headers are parsed with the Step 7 Tree-sitter parser. Rust
`pub fn` declarations and complete bodies are read with a small deterministic
scanner that understands balanced braces, comments, and quoted literals. Each
SDK function retains its signature, source path, line range, and source-file
SHA-256.

Exact public function names map only when precisely one Rust crate exports the
name. Six reviewed rules cover the SDK's C default-initializer pattern. Five
create the corresponding configuration type, while one initializes the
one-wire object itself:

```text
*_configure_default(&config) -> *_config_t::default()
one_wire_configure_default(&object) -> one_wire_t::default()
```

The rule is accepted only when the expected Rust crate and exported type both
exist. It is not a fuzzy or AI-generated match. C APIs without an exact target
or reviewed adapter are marked `unsupported`.

The current checked-in SDK references contain:

- 105 public C functions;
- 65 public Rust functions with exact C-name matches;
- 6 reviewed default-construction adapters;
- 34 unsupported C functions.

The unsupported total describes the complete SDK pair. It does not block a
package unless that package actually calls one of those functions. CAN, DMA,
and RTC account for most of the current SDK-wide gap.

### Run the SDK resolver

```bash
PYTHONPATH=src .venv/bin/python examples/step_08_sdk_mapping.py
```

For the cached IPS Display 2 package, the example classifies 44 distinct calls:

- 23 package-local function calls and 1 package-local macro;
- 8 direct Rust SDK calls and 1 SDK default-construction adapter;
- 5 platform timing/startup adapters;
- 5 logging adapters;
- 1 C-standard-library adapter.

No calls remain unresolved. The required Rust crates are
`drv_digital_out`, `drv_spi_master`, and `system`.

Print the complete package translation plan:

```bash
PYTHONPATH=src .venv/bin/python examples/step_08_sdk_mapping.py --json
```

Print the reusable SDK-wide mapping database instead:

```bash
PYTHONPATH=src .venv/bin/python examples/step_08_sdk_mapping.py --sdk-json
```

Resolve another package already in the cache by passing its name. Add
`--download` only when you explicitly want to permit an ordinary HTTPS
download:

```bash
PYTHONPATH=src .venv/bin/python examples/step_08_sdk_mapping.py \
    "PACKAGE NAME" --download
```

When debugging in VS Code, open `examples/step_08_sdk_mapping.py` and press
`F5`. No arguments are required for the cached IPS Display 2 package.

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: forty-two tests finish with `OK`. The Step 8 tests cover exact
matches, reviewed default adapters, unsupported functions, balanced Rust-body
scanning, complete reference-SDK counts, package-local functions/macros,
platform timing, required crates, and unresolved-call blocking.

SDK indexing and resolution read only checked-in local files. They perform no
network requests, write no generated output, require no OpenAI key, and consume
no AI-model tokens. As before, only the example's explicit `--download` option
permits normal HTTPS and cache writes.

## Step 9: Build bounded model context

Step 9 adds a versioned `ModelRequest` containing exactly two
provider-independent messages:

1. A fixed trusted system message defines the conversion and safety rules.
2. A user message contains one canonical, one-line JSON object explicitly
   labeled as untrusted conversion data.

JSON encoding escapes source newlines and quotes. Therefore a downloaded
comment containing text such as `END_UNTRUSTED_CONVERSION_DATA_JSON` remains
inside a JSON string and cannot create a second boundary line. The system
instructions also explicitly prohibit treating source data as instructions.

The compact context contains:

- package identity, version, archive hash, and source hashes;
- every parsed function signature and complete implementation body;
- public function documentation, types, enums, typedefs, globals, macro
  definitions, and preprocessor conditions;
- external SDK, platform, logging, and standard-library resolutions;
- metadata for large resources, without embedding their bytes;
- only the Rust SDK function signatures and bodies selected by direct Step 8
  mappings.

It excludes generated HTML, duplicate package trees, raw parser AST nodes,
repetitive macro documentation, redundant local-call resolutions, unrelated
Rust SDK crates, and large resource arrays.

`build_model_request` refuses to continue if the translation plan contains an
unresolved call. It also enforces a configurable request-content limit, which
defaults to 256 KiB. The request records a prompt version, canonical-context
SHA-256, exact UTF-8 content byte count, and a rough byte-based input-token
estimate. The estimate is useful for comparing requests but is not a provider
billing value; actual token usage will come from the future model response.

### Build the IPS Display 2 request locally

```bash
PYTHONPATH=src .venv/bin/python examples/step_09_context.py
```

The current request contains four C source files and only eight Rust SDK
functions. It is approximately 110 KB, with a rough estimate of 27,500 input
tokens, and remains below the default 256 KiB safety limit.

Print the complete `ModelRequest`, including its trusted instructions and
untrusted JSON payload:

```bash
PYTHONPATH=src .venv/bin/python examples/step_09_context.py --json
```

Exercise the local size gate without making an API request:

```bash
PYTHONPATH=src .venv/bin/python examples/step_09_context.py \
    --max-request-bytes 100
```

This exits with a concise error explaining the measured size and configured
limit.

Resolve another cached package by passing its name. Add `--download` only when
you explicitly want to permit an ordinary HTTPS package download:

```bash
PYTHONPATH=src .venv/bin/python examples/step_09_context.py \
    "PACKAGE NAME" --download
```

When debugging in VS Code, open `examples/step_09_context.py` and press `F5`.
No arguments are required for the cached IPS Display 2 package.

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: forty-five tests finish with `OK`. Step 9 tests cover stable
context hashes, strict system/user message ordering, untrusted delimiter text,
relevant-only Rust SDK selection, unresolved-call rejection, and request-size
enforcement.

Context construction reads only local parsed structures and SDK references. It
does not write files, access the network, require an OpenAI key, or consume AI
tokens. As before, only the example's explicit `--download` option permits
ordinary HTTPS and cache writes.

## Step 10: Validate output through an offline model-client boundary

Step 10 adds three related boundaries:

- `ModelOutput` is the only data the model may generate. It contains
  `library.rs`, `main.rs`, required Rust crate names, a source-API coverage
  ledger, and explicit assumptions, warnings, and unsupported items.
- `ModelConversion` attaches the model ID, provider response ID, originating
  context hash, prompt version, actual usage counters, and latency.
- the asynchronous `ModelClient` protocol lets the pipeline use a fake in
  tests and a real provider in a later step.

The model does not generate `Cargo.toml` or `mikrobus.rs`. Those remain inputs
to the deterministic renderer planned for a later step. The parser also does
not extract JSON from prose or Markdown fences: the complete response must be
one valid object with exactly the schema's fields. Runtime validation adds
rules that JSON Schema alone does not conveniently express, including required
`no_std`/`no_main` and local module declarations, unique source-symbol coverage,
and a written explanation for every unsupported item.

The closed schema design follows OpenAI's Structured Outputs requirements that
object fields be required and objects use `additionalProperties: false`:

<https://developers.openai.com/api/docs/guides/structured-outputs>

### Run the offline fake against the IPS Display 2 request

```bash
PYTHONPATH=src .venv/bin/python examples/step_10_fake_model.py
```

The command rebuilds the real local request and passes it to `FakeModelClient`.
It reports `fake-local`, retains the request hash and prompt version, and shows
zero input and output tokens. The returned Rust is deliberately labeled as an
incomplete fixture; it demonstrates validation and provenance, not translation
quality.

Print the complete fake `ModelConversion`:

```bash
PYTHONPATH=src .venv/bin/python examples/step_10_fake_model.py --json
```

When debugging in VS Code, open `examples/step_10_fake_model.py` and press
`F5`. No arguments are required for the cached IPS Display 2 package.

If the package is not cached, the command exits without network access. Add
`--download` only when you explicitly want to permit the same ordinary HTTPS
package downloader used by earlier steps:

```bash
PYTHONPATH=src .venv/bin/python examples/step_10_fake_model.py --download
```

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: fifty-one tests finish with `OK`. The six new tests cover
valid strict JSON, deterministic crate ordering, unknown fields, Markdown
wrappers, required Rust module declarations, unsupported-item explanations,
request provenance, sequential fake response IDs, and zero fake token usage.

The normal Step 10 example reads local files and the existing validated cache.
It requires no OpenAI key, makes no OpenAI API call, writes no generated Rust
files, and consumes no model tokens. Only its explicit `--download` option can
make an ordinary HTTPS request and write package data under `.cache`.

## Step 11: Add the explicitly enabled OpenAI client

Step 11 implements `OpenAIModelClient` with the OpenAI Responses API. The
cost-sensitive default is exactly `gpt-5.6-luna`; `OPENAI_MODEL` or `--model`
can select another non-Sol model. Both `gpt-5.6-sol` and its `gpt-5.6` alias are
rejected so an environment setting cannot accidentally move this converter to
the expensive Sol tier.

The API request uses:

- the two system/user messages built in Step 9;
- `text.format` with a provider-compatible form of the strict Step 10 schema;
- low reasoning effort and a 32,768-token maximum output by default;
- disabled server-side response storage and input truncation;
- the context SHA-256 as a stable prompt-cache key;
- no tools and no automatic retry;
- a 180-second whole-request timeout.

Completed responses must include output text and token usage. Refusals,
incomplete or failed statuses, malformed structured data, and missing
provenance are explicit errors. The API key is read only from
`OPENAI_API_KEY`; it is never accepted as a command argument, serialized,
printed, or written to the cache.

The implementation follows the current official documentation for the
Responses API and Structured Outputs:

- <https://developers.openai.com/api/reference/resources/responses/methods/create>
- <https://developers.openai.com/api/docs/guides/structured-outputs>
- <https://developers.openai.com/api/docs/models/gpt-5.6-luna>

### Preview safely without an SDK or API key

```bash
PYTHONPATH=src .venv/bin/python examples/step_11_openai.py
```

This rebuilds the cached IPS Display 2 request and prints the selected model,
approximate input size, output cap, reasoning effort, and timeout. It does not
import the OpenAI SDK or make an API request because `--yes-use-openai` is
absent.

### Install the OpenAI SDK

```bash
.venv/bin/python -m pip install openai==2.29.0
```

This accesses the Python package index and writes packages inside `.venv`. It
does not make an OpenAI API request and consumes no model tokens. The version
is pinned in the root `pyproject.toml` and matches the checked-in Open WebUI
reference environment.

### Explicitly run one paid conversion request

First provide the key without putting it in a command-line argument:

```bash
read -rsp "OpenAI API key: " OPENAI_API_KEY
export OPENAI_API_KEY
echo
```

Then authorize one request:

```bash
PYTHONPATH=src .venv/bin/python examples/step_11_openai.py \
    --yes-use-openai
```

This sends the Step 9 message contents to OpenAI and consumes billable input,
output, and possibly reasoning tokens. It prints actual usage after a completed
response. It validates but does not write the generated Rust files. Add
`--json` to print the complete response locally.

Remove the key from the shell when finished:

```bash
unset OPENAI_API_KEY
```

For VS Code debugging, put `OPENAI_API_KEY=...` in a local `.env` file, which
is now ignored by Git, and add this property to the selected launch
configuration:

```json
"envFile": "${workspaceFolder}/.env"
```

Add `"--yes-use-openai"` to `args` only when you intend to make a paid call.
Without that argument, `F5` remains a local preview.

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: fifty-eight tests finish with `OK`. The seven new tests use a
local stub, not the OpenAI SDK or network. They cover the opt-in gate, Luna
default, Sol rejection, strict request shape, disabled response storage, token
usage, incomplete responses, refusals, malformed output, and timeouts.

The test suite and default example remain offline and consume no model tokens.
The explicitly supplied `--download` option can separately permit an ordinary
HTTPS package download, while only `--yes-use-openai` permits the model call.

## Step 12: Deterministically render the four-file package

Step 12 adds the `render_package` stage and versioned `RenderedPackage` report.
The renderer accepts a `TranslationPlan`, its exact `ModelRequest`, and the
resulting `ModelConversion`. Before any directory is created, it verifies:

- the translation plan contains no unresolved external calls;
- request source paths belong to that plan;
- response context hash and prompt version match the request;
- model-reported Rust SDK crates exactly match the deterministic plan;
- no coverage entry or diagnostic reports unsupported functionality;
- every public driver-header function, meaningful non-empty macro, semantic
  struct/union/enum or standalone typedef, public global, and deferred resource
  has a coverage-ledger entry;
- the four rendered files remain below the configurable 4 MiB total limit.

Empty preprocessor definitions are excluded from required coverage because the
current package uses them as header guards. A typedef generated from the same
named struct or enum is treated as one semantic type instead of demanding two
duplicate coverage decisions.

The model supplies only `library.rs` and `main.rs`. The renderer creates the
same lightweight `Cargo.toml` workspace marker used by the checked-in Rust
examples and a replaceable `mikrobus.rs` containing exactly these mikroBUS 1
signals:

```text
AN, RST, CS, SCK, MISO, MOSI, PWM, INT, RX, TX, SCL, SDA
```

Every default pin value is `0xFF`; there are no guessed `GPIO_*` values or
additional sockets.

Files are first written to a private staging directory and then published by a
single directory rename. Repeating an identical render performs no writes and
reports `reused_existing: true`. If any existing file differs, is missing, is
extra, or is a symbolic link, rendering stops without overwriting it.

### Render the offline demonstration

```bash
PYTHONPATH=src .venv/bin/python examples/step_12_render.py
```

The first run creates:

```text
output/step_12/ips-display-2/
├── Cargo.toml
├── library.rs
├── main.rs
└── mikrobus.rs
```

It reports four files, their sizes and hashes, and 294 coverage entries found
in the current IPS Display 2 package. Run the same command again to see the
safe `reused identical` path.

The generated Rust is deliberately a tiny offline fixture demonstrating the
renderer; it is not presented as a translated or validated IPS Display 2
driver. The later orchestrator will instead pass the validated Step 11 model
result to this same renderer.

Print the complete provenance and coverage report:

```bash
PYTHONPATH=src .venv/bin/python examples/step_12_render.py --json
```

Check that Cargo accepts the workspace-marker TOML:

```bash
cargo metadata --no-deps --format-version 1 \
    --manifest-path output/step_12/ips-display-2/Cargo.toml
```

When debugging in VS Code, open `examples/step_12_render.py` and press `F5`.
No arguments are required for the cached package. Use `--output-root PATH` to
render the fixture somewhere else. If the package is not cached, only the
explicit `--download` argument permits the ordinary HTTPS download.

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: sixty-five tests finish with `OK`. Seven renderer tests cover
the exact file set, TOML metadata, twelve blank pin declarations, idempotent
reuse, conflict preservation, provenance and dependency mismatches, incomplete
and unsupported coverage, and the total-size limit.

The Step 12 demonstration writes only beneath the ignored `output` directory.
It normally reads the existing package cache, performs no OpenAI API request,
requires no API key, and consumes no model tokens. Its optional `--download`
path is the same ordinary HTTPS package download used in earlier steps.

## Step 13: Validate rendered artifacts locally

Step 13 adds `validate_rendered_package` and a versioned `ValidationReport`.
This gate is deliberately local and read-only. It performs these checks:

1. `artifact_integrity` verifies that the output still contains exactly the
   four regular, non-symbolic-link files recorded by `RenderedPackage`. Every
   size and SHA-256 digest must match. The deterministic `Cargo.toml` and
   `mikrobus.rs` contents are checked again as well.
2. `rustfmt` runs with `--check`, Rust 2024 edition, and
   `skip_children=true`. This detects Rust syntax errors and formatting
   differences without changing any source file.
3. `cargo_metadata` runs with `--offline --no-deps`, validates the generated
   workspace marker, and confirms that `main.rs` remains the selected entry.

If artifact integrity fails, both tools are skipped. Otherwise, a failure in
one tool does not hide the other tool's diagnostics. Missing executables,
timeouts, invalid Cargo JSON, and nonzero exits become structured failed checks
instead of uncaught subprocess errors. Captured stdout and stderr are limited
to 64 KiB per stream.

The report records the four file hashes, source and request provenance, exact
commands, local tool versions, diagnostics, and an overall local `passed`
value. It always records `sdk_compilation_status: not_run`. This is important:
a passing Step 13 report proves local syntax/format/workspace checks only. It
does not claim that the code compiles for a board or preserves device behavior.

### Run the local validation demonstration

Both `rustfmt` and `cargo` must be available on `PATH`:

```bash
PYTHONPATH=src .venv/bin/python examples/step_13_validate.py
```

The command renders a formatted offline fixture beneath:

```text
output/step_13/ips-display-2/
```

Expected result: all three checks show `PASSED`. Repeating the command safely
reuses the identical rendered package. The fixture is intentionally small and
does not represent a translated IPS Display 2 driver.

Print the complete machine-readable report:

```bash
PYTHONPATH=src .venv/bin/python examples/step_13_validate.py --json
```

To see how an unavailable tool is reported without changing project files:

```bash
PYTHONPATH=src .venv/bin/python examples/step_13_validate.py \
    --output-root output/step_13_missing_tool \
    --rustfmt rustfmt-does-not-exist
```

This intentional failure exits with status 1. Cargo still runs and reports its
own independent result. Use `--timeout SECONDS`, `--rustfmt PATH`, or
`--cargo PATH` to control local tool execution.

When debugging in VS Code, open `examples/step_13_validate.py` and press `F5`.
The existing launch configuration selects `.venv`, sets the workspace as the
working directory, and adds `src` to `PYTHONPATH`.

### Run all tests

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

Expected result: seventy tests finish with `OK`. The five new validator tests
cover a successful report, artifact tampering, formatting failure, missing
tools, invalid Cargo metadata, offline mode, and the rule that independent
checks continue after a tool failure.

The demonstration normally reads the existing package cache and writes only
beneath the ignored `output` directory. Validation never accesses the network,
requires no API key, makes no OpenAI request, and consumes no model tokens. As
in earlier examples, only an explicit `--download` can permit an ordinary HTTPS
package download if the selected package is absent from the local cache.
