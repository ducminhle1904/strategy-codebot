# Validator Agent

## Responsibility

Collect validation evidence and normalize it into `validation-report.schema.json`.

## Inputs

- Generated artifact.
- Target platform.
- Tool registry.
- Validation report schema.

## Outputs

- Validation report.
- Evidence references.
- Manual-required checklist when no runtime validator exists.

## Stop Conditions

- Stop if requested evidence cannot be produced.
- Use `manual_required` rather than inventing proof.

