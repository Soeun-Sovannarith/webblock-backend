# 0004 Generation capture and admission

## Decision

- **Contracts.** A generation contract freezes, per product type, that type's active schema with the highest
  `version_number`. Each type must belong to the project's business type and be active (else 422
  `PRODUCT_TYPE_NOT_ALLOWED`). `apiContractVersion` comes from configuration
  (`webblock.generation.api-contract-version`); the client never sends it. Contract creation has no idempotency key:
  a retried `POST` freezes another version. The contract code lives in the `generation` feature.
- **Hashes.** SHA-256 of the RFC 8785 (JCS) form, library `java-json-canonicalization`: `request_hash` over the
  capture body, `captured_input_hash` over `capturedInput` (the same hash Codegen checks).
- **Capture.** One transaction under the project row lock. Idempotency answers before the project state check: a
  known `Idempotency-Key` replays `200` with `{ generationRequestId, status }`, the status read from the row now. The
  same key with another body is 409 `IDEMPOTENCY_KEY_REUSED`. One request in flight per project (409
  `GENERATION_IN_PROGRESS`).
- **Admission.** A Namastack durable job (`generation:<requestId>`) scheduled in the capture transaction asks Payment.
  Admitted: `captured -> generating` and `project.generation_requested.v1` in one transaction. Refused: `failed` with
  Payment's reason in lower case if it matches `[a-z_]{1,64}`, else `admission_error`. Unavailable (timeout,
  connection, 5xx): backoff 5 s, 10 s, 30 s, then every 60 s, for a window from capture
  (`webblock.generation.admission-window`, default 15 minutes), then `admission_unavailable`. Any other answer:
  `admission_error` at once.
- **Lost answer.** If Payment admitted but no answer arrived within the window, the request fails and Payment has
  counted the attempt. Accepted limit; crediting it back is a later Payment reconciliation.
- **Catalog needed.** The contract list, the contract `201` and `codegen-input` read the catalog. Without it they
  answer `503 CATALOG_UNAVAILABLE`.

## Reason

The captured input must be exactly what Codegen builds and what Payment counted. A hash over a canonical form is
language independent, and one durable job per request survives restarts without a hand-written retry loop.

## Status

Accepted. Built and tested. Open for the next phase: `ai/handoffs/next.md`.
