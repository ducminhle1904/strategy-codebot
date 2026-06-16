# 0015 - CLI Productization With GitHub Artifacts

## Status

Accepted

## Context

Phase 4 provides useful CLI workflows and evidence artifacts, but the repository still needs product-level packaging, onboarding, release checks, and CI before it can be used reliably by other engineers.

## Decision

Productize Phase 5 as a CLI release path with source install instructions, doctor checks, MIT license, package metadata, GitHub Actions CI, and GitHub artifact builds.

Do not add an API service, web UI, PyPI publishing, Pine runtime validation, MT5 compile/test automation, broker integration, or live trading in Phase 5.

## Consequences

The project can produce wheel and source distribution artifacts through GitHub Actions without requiring PyPI credentials. Product readiness is proven through local `doctor`, CI checks, and dry-run smoke artifacts.
