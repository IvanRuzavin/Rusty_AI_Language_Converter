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
