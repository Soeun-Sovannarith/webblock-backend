# 0003 HTTP and event formats

## Decision

- REST request and response bodies use **camelCase**. RabbitMQ events use **snake_case**, as Provisioning's do.
- Every successful JSON response uses the envelope `{ success, data, meta }` (no `error` member).
- Every error is RFC 9457 Problem Details: `type` `urn:webblock:problem:<code in kebab-case>`, `title`, `status`,
  `instance`, and the extensions `code`, `errors` (`[{ field, code, message }]`), `requestId` and `traceId`. There is
  no `detail`: an error never echoes caller input.
- Content type: `application/problem+json` when `Accept` allows it, or `application/json` with the same body to a
  client that accepts only that. An error is never answered 406. Provisioning follows the same rule; both services pin
  one shared table of cases (`ProblemDetailFactoryTest`).
- IDs have typed names in every body and path variable (`projectId`, `pageId`, `generationRequestId`,
  `generationContractId`), never a bare `id`.
- Payment keeps its own `{ success, data, error, meta }` shape (`.claude/docs/contracts/payment-contract.md`).

## Reason

One error shape across Project and Provisioning lets clients and MCP tools handle errors the same way. Events match
the existing Provisioning contracts, so no consumer needs a mapping.

## Status

Accepted. Built and tested.
