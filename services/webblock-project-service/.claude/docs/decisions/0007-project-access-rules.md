# 0007 Project access rules

## Decision

- Only the owner (`owner_user_id`) manages a project. Operators (`ADMIN`) are authorised by Provisioning itself
  before it calls the access check.
- A suspended project allows the owner's reads (including the release history) and refuses every change.
- A deleted project (`deleted_at` set, row kept) is unknown to every call, the owner's included: REST answers 404
  `PROJECT_NOT_FOUND`. `PROJECT_DELETED` stays in the `ErrorCode` enum and the access reason list for contract
  stability and is no longer answered.
- The access check answers an unknown or deleted project with `allowed: false`, reason `NOT_FOUND`, never 404, so it
  does not reveal whether a project exists.
- One exception to "not the owner is 404": creating a website (`PUT /api/v1/projects/{projectId}`) on a project ID
  that already exists answers 409 `PROJECT_ID_CONFLICT` to every caller who is not repeating the same create (the
  same owner, name, sub-domain, business type and template), a stranger included. A deleted project's ID is never
  reused: the owner's repeat create of it is 409 too.
- The home page `/` may be renamed or deleted like any other page.
- Business type codes use the `*_shop` form (`clothing_shop`).

## Reason

One owner per project keeps the access check simple and the same for Provisioning and the sites. Read-only access
while suspended lets an owner see what is there without changing anything. A deleted website is gone for every caller
and every service; its row is kept for history. The client chooses the project ID at
creation, so a creator must learn that the ID is taken: a 404 would tell it nothing it can act on. The 409 reveals
only that the ID is in use, not who owns it or what it holds.

## Status

Accepted. Built and tested.
