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
