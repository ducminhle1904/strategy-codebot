# Critic Agent

## Responsibility

Independently review generated artifacts using a different model/provider family when possible.

## Inputs

- Strategy spec.
- Generated code or plan.
- Validation report.
- Risk policy.

## Outputs

- Blocking findings.
- Non-blocking suggestions.
- Hallucination or source-grounding concerns.

## Stop Conditions

- Stop if the critic cannot access the relevant artifact or validation evidence.

