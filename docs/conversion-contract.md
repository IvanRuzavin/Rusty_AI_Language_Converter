# Conversion Contract

This document defines when a C-to-Rust Click conversion may be called
successful. It is the contract shared by the parser, AI model, renderer, and
validator.

## 1. Unit of conversion

One conversion starts from one Click package selected from
`metadata_clicks_c.json`. The package manifest determines its canonical
libraries and examples. Duplicate source trees, generated HTML documentation,
and unrelated build artifacts are not separate conversion inputs.

Every run must retain provenance for the metadata entry, package manifest,
package version, download URL, archive checksum, input files, and converter
version.

## 2. Meaning of preserved functionality

Rust code does not need to reproduce the C syntax or memory layout unless an
external interface requires it. It must preserve externally observable driver
behavior, including:

- supported device capabilities and public operations;
- register addresses, command values, masks, and default values;
- GPIO state changes and their ordering;
- SPI, I2C, UART, PWM, ADC, and one-wire configuration and transactions;
- chip-select, reset, interrupt, and enable behavior;
- delay durations and ordering when they are operationally significant;
- initialization defaults and state transitions;
- input validation, output values, and error conditions;
- algorithms such as scaling, decoding, checksums, and unit conversion;
- behavior demonstrated by the package's canonical example.

Improving memory safety is allowed. For example, a C pointer plus length may
become a Rust slice and an `err_t` result may become `Result`. Such changes must
not silently remove valid behavior or introduce new successful behavior for
invalid operations.

## 3. API coverage ledger

Every public C function, type, enum, and meaningful macro must appear in a
coverage ledger with exactly one status:

- `translated`: represented directly in the Rust output;
- `replaced_by_rust_construct`: represented by a documented Rust mechanism,
  such as `Default` or an enum;
- `private_internal`: intentionally implementation-only in both versions;
- `not_applicable`: build-only or language-only construct with no runtime
  meaning;
- `unsupported`: cannot be translated with the available Rust SDK.

A package with an `unsupported` public capability is not a successful
conversion. It must be reported as blocked or sent for human review.

Renaming a public operation is permitted only when the ledger records the C and
Rust names. The first implementation should prefer recognizable Click API names
so conversions remain easy to compare with their C sources.

## 4. Required output files

### `library.rs`

`library.rs` contains the Click driver and must:

- compile in a `no_std` embedded project;
- use only verified Rust SDK APIs and declared local definitions;
- preserve relevant package license and attribution notices;
- contain no unresolved C types, invented SDK symbols, or placeholder bodies;
- avoid target-board pin values; pins come from configuration supplied by
  `main.rs` and `mikrobus.rs`.

Large static resources may be emitted as separate binary assets later if the
build contract permits it. Their checksums and byte ordering must be preserved.

### `main.rs`

`main.rs` translates the canonical C example. At minimum it contains:

```rust
#![no_std]
#![no_main]

mod library;
mod mikrobus;

use library::*;
use mikrobus::*;
```

It must preserve the example's initialization, repeated task behavior, delays,
and meaningful device operations. Logging may be adapted to available Rust
facilities, but removing logs must not remove device operations or timing.

### `Cargo.toml`

`Cargo.toml` is rendered from a validated dependency list. For the current
MikroBUS Rust Tools convention it begins as a workspace marker with
`workspace.metadata.mikrobus-rust.entry = "main.rs"`. The renderer, rather than
the AI model, owns TOML syntax and machine-specific paths.

### `mikrobus.rs`

The default file is deterministic and replaceable by the extension. It contains
the standard mikroBUS 1 signal constants:

```text
AN, RST, CS, SCK, MISO, MOSI, PWM, INT, RX, TX, SCL, SDA
```

Each default value is `0xFF`. It must contain no guessed MCU or board mapping.
The extension may replace it with one or more real socket mappings.

## 5. Model-output contract

The model returns structured data, not Markdown code fences. Its result
contains:

- generated `library.rs` content;
- generated `main.rs` content;
- required Rust SDK dependencies;
- the API coverage ledger;
- assumptions, warnings, and unsupported items.

The converter validates this response before writing files. `Cargo.toml` and
the default `mikrobus.rs` are deterministic products assembled by the
converter, even though all four files are returned to the caller as the final
conversion result.

## 6. Acceptance gates

A conversion is accepted only if all applicable gates pass:

1. The model response matches the expected schema.
2. Every public input symbol is accounted for.
3. Every external call resolves to an available Rust SDK API.
4. Generated Rust parses and passes formatting checks.
5. The complete generated project passes `cargo check` in the extension's SDK
   environment.
6. Constants, defaults, resources, and dependency declarations match the
   parsed source facts.
7. Deterministic or mock-based behavior tests show equivalent operations.
8. No unresolved assumptions affect device behavior.

Compiler or validation failures may trigger a bounded, diagnostic-driven repair
attempt. Repeated failure is reported for human review; it is never converted
into a false success.

## 7. Security and reproducibility

- Archives are validated before extraction and cannot write outside their
  assigned cache directory.
- Binary size, expanded size, file count, and path limits are enforced.
- Downloaded source comments and documentation are treated as untrusted data,
  not as model instructions.
- API keys and other secrets are read from secure configuration and never
  included in prompts or reports.
- Input hashes, schema versions, prompt versions, model identifiers, and
  validation results are recorded so a run can be audited and reproduced.
