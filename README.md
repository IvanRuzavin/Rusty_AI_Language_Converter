# Rusty AI Language Converter

Rusty AI Language Converter is a planned conversion pipeline for translating
MikroE Click packages from C to Rust while preserving their observable
behavior.

The project will combine deterministic source processing with an AI-assisted
translation stage. Downloading, archive inspection, parsing, dependency
resolution, file rendering, and validation remain ordinary program logic. The
AI model receives a compact, structured representation of one Click package
and only the parts of the C and Rust SDKs that package needs.

Package downloads are normal HTTP requests made by the converter. They do not
use an AI model or consume model tokens.

## Intended workflow

```text
metadata_clicks_c.json
    -> download and cache package
    -> safely inspect and deduplicate archive
    -> parse C source into a versioned intermediate representation
    -> resolve C SDK calls to Rust SDK calls
    -> build package-specific model context
    -> generate Rust driver and example code
    -> render deterministic project files
    -> compile and compare behavior
    -> accept, repair, or require human review
```

The required output for a converted package is:

```text
<click-name>/
├── Cargo.toml
├── library.rs
├── main.rs
└── mikrobus.rs
```

- `library.rs` contains the translated Click driver.
- `main.rs` contains the translated example application and imports both local
  modules.
- `Cargo.toml` is compatible with the project layout expected by MikroBUS Rust
  Tools.
- `mikrobus.rs` is a replaceable default mapping. Until the extension writes a
  real board mapping, its standard mikroBUS 1 pins use `0xFF`.

The detailed correctness rules are in
[`docs/conversion-contract.md`](docs/conversion-contract.md). The planned
component boundaries and data flow are in
[`docs/architecture.md`](docs/architecture.md).

Completed steps and commands you can run yourself are recorded in
[`docs/progress.md`](docs/progress.md).

## Reference material

- `metadata_clicks_c.json` lists the downloadable C Click packages.
- `references/c_sdk` contains the available C driver SDK reference.
- `references/rust_sdk` contains the available Rust driver SDK reference.
- `references/examples` contains Rust integration and style examples.
- `references/openwebui` contains parsing and model-integration experiments
  that can be selectively adapted.

Reference code is evidence, not an automatic correctness oracle. Some C APIs
currently have no Rust counterpart, and existing examples may intentionally
implement only a subset of their original package.

## Current status

The repository contains its initial Python package, catalog data models, a
validated local metadata loader, an HTTPS archive downloader with a
content-addressed cache, safe extraction-free archive inventory, and
manifest-driven canonical source selection, a Tree-sitter-based C intermediate
representation, and deterministic C-to-Rust SDK call resolution. No context
builder, model client, or conversion command has been implemented yet.

The future model identifier will be configurable through `OPENAI_MODEL`. The
cost-sensitive default is `gpt-5.6-luna`; the converter will not silently
upgrade a run to a Sol model.

Step 7 introduces the first third-party runtime dependencies. Install the
pinned parser wheels in the configured virtual environment:

```bash
.venv/bin/python -m pip install --only-binary=:all: \
    tree-sitter==0.26.0 tree-sitter-c==0.24.2
```

Then run the complete test suite:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

To see the current catalog models operate on an example record:

```bash
PYTHONPATH=src python3 examples/step_02_models.py
```

To load and search the complete local Click catalog:

```bash
PYTHONPATH=src python3 examples/step_03_catalog.py
```

To preview the package downloader without making a network request:

```bash
PYTHONPATH=src python3 examples/step_04_download.py
```

After downloading a package, inspect and classify it without extracting files:

```bash
PYTHONPATH=src python3 examples/step_05_inspect.py
```

To parse package metadata and load only canonical source text:

```bash
PYTHONPATH=src python3 examples/step_06_sources.py
```

To parse those sources into `ClickPackageIR`:

```bash
PYTHONPATH=src .venv/bin/python examples/step_07_parse_c.py
```

To index both reference SDKs and resolve the parsed package's calls:

```bash
PYTHONPATH=src .venv/bin/python examples/step_08_sdk_mapping.py
```

API credentials must be provided through the environment or a secret manager.
They must never be committed, stored in generated packages, or included in
diagnostic logs.
