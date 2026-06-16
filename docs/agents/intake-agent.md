# Intake Agent

## Responsibility

Convert a user strategy request into a structured strategy spec.

## Inputs

- Raw user prompt.
- Product boundary docs.
- `schemas/strategy-spec.schema.json`.

## Outputs

- Valid strategy spec JSON.
- Missing information questions when required fields are absent.
- Risk lane suggestion.

## Stop Conditions

- Stop if the request asks for live trading or broker credentials.
- Stop if target platform cannot be inferred and the user did not provide enough detail.

