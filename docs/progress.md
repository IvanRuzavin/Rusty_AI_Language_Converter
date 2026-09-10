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
