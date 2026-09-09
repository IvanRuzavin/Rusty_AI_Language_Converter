# Architecture

The converter is organized as a sequence of independently testable stages.
Each stage consumes a versioned data structure and produces either a validated
result or explicit diagnostics.

## Pipeline overview

```text
Package catalog
    |
    v
Downloader and cache
    |
    v
Archive classifier -----> ignored/duplicate/resource inventory
    |
    v
C parser and normalizer -----> ClickPackageIR
    |                              |
    |                              v
    +----------------------> SDK resolver
                                   |
                                   v
                            TranslationPlan
                                   |
                                   v
                            Context builder
                                   |
                                   v
                             Model client
                                   |
                                   v
                            Artifact renderer
                                   |
                                   v
                         Compiler and validators
                                   |
                    +--------------+--------------+
                    |                             |
                  accept                    repair/review
```

## Components

### 1. Package catalog

Loads `metadata_clicks_c.json`, validates its shape, normalizes package names,
and exposes stable package records. It does not download anything.

Primary output: `PackageRecord`.

### 2. Downloader and cache

Downloads a selected package, validates the response, computes a checksum, and
stores the immutable archive in a content-addressed cache. Repeated conversions
reuse the same archive.

This component uses a normal HTTP client. It has no model-client dependency and
never sends package bytes or URLs to an AI model merely to download them.

Primary output: `CachedArchive`.

### 3. Archive classifier

Inspects ZIP entries safely and uses the package manifest plus build files to
identify canonical headers, implementations, examples, resources, compile
definitions, documentation, and duplicates. It does not send or extract every
file indiscriminately.

The implemented first layer validates archive paths and entry types, applies
expansion limits, hashes contents, detects duplicates, and assigns preliminary
roles. Manifest interpretation and canonical-file selection are the next layer
of this component.

Primary output: `PackageInventory`.

### 4. C parser and normalizer

Parses canonical C headers and sources with Tree-sitter. It retains raw source
spans while extracting declarations, macro bodies, preprocessor conditions,
Doxygen documentation, function bodies, references, and call relationships.

Regex parsing is allowed only as a recovery mechanism that emits reduced-
confidence diagnostics.

Primary output: `ClickPackageIR`.

### 5. SDK resolver

Uses a separately generated C-to-Rust SDK mapping database. It resolves every
external C dependency and selects only the Rust SDK evidence relevant to the
current package.

If a required operation has no verified Rust equivalent, resolution stops and
reports the missing capability before model tokens are spent.

Primary output: `TranslationPlan`.

### 6. Context builder

Produces a compact model request from the intermediate representation and
translation plan. Stable translation rules and SDK mappings are placed before
package-specific content. Generated HTML, duplicate files, irrelevant SDK
drivers, and large resource arrays are excluded.

Primary output: `ModelRequest`.

### 7. Model client

Owns communication with the configured OpenAI model. It requests structured
output, handles refusals and incomplete results, records token/latency metadata,
and returns parsed data. Credentials are supplied externally.

The model ID is read from `OPENAI_MODEL`. The initial cost-sensitive default is
`gpt-5.6-luna`, and no automatic fallback may select a Sol model.

The rest of the converter depends on a small model-client interface so the
pipeline can be tested with a fake client and is not coupled to Open WebUI.

Primary output: `ModelConversion`.

### 8. Artifact renderer

Validates model-provided Rust contents and writes the final package atomically.
It deterministically creates `Cargo.toml` and the default `mikrobus.rs`, then
records provenance and coverage in a conversion report.

Primary output: `RenderedPackage`.

### 9. Compiler and validators

Runs formatting, syntax, dependency, compilation, API-coverage, and behavior
checks. A behavior harness will compare C and Rust driver activity through mock
HAL/SDK layers where practical.

Primary output: `ValidationReport`.

### 10. Orchestrator

Coordinates stages, persists state, supports retries, and decides whether a
package is accepted, repaired, blocked, or assigned for human review. It is the
entry point used by a future CLI or extension integration.

## Intermediate representation boundaries

The first schemas will cover these records:

```text
PackageRecord
CachedArchive
PackageInventory
ClickPackageIR
SdkMapping
TranslationPlan
ModelConversion
ValidationReport
```

These records will carry `schema_version` fields. Filesystem paths are kept
separate from model-visible logical paths, and every extracted symbol retains
its source file and line range.

The model does not receive a serialized parser AST. It receives normalized
facts plus complete source slices for the functions it must translate. This
preserves implementation detail without spending context on parser internals.

## Deterministic and AI-assisted responsibilities

Deterministic code owns:

- metadata and manifest parsing;
- downloading, checksums, caching, and safe extraction;
- source classification and duplicate detection;
- C parsing and source provenance;
- SDK symbol resolution;
- large resource conversion;
- `Cargo.toml` and default `mikrobus.rs` rendering;
- formatting, compilation, coverage, and behavior validation.

The AI model owns:

- mapping C implementation logic into valid Rust;
- choosing safe Rust representations within the conversion contract;
- translating the canonical example;
- explaining assumptions and unresolved semantic questions.

## Proposed implementation layout

The exact module names will be reviewed before executable code is added. The
initial target layout is:

```text
src/rusty_ai_converter/
├── catalog.py
├── download.py
├── archive.py
├── models.py
├── parsing/
├── sdk_mapping/
├── context.py
├── model_client.py
├── render.py
├── validate.py
└── orchestrator.py

schemas/
tests/
docs/
```

The next project step is selective content loading, manifest interpretation,
and canonical-file selection before C parsing begins.
