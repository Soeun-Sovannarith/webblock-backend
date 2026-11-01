# The pinned copy of web-block-contracts (decision 0022 E3), included by the Makefile with one line.
# contracts.lock holds the commit; CONTRACTS_REPO is the local clone (default ../web-block-contracts).
# contracts-sync writes src/contracts/shared/ and src/provisioning/infrastructure/grpc/_gen/ from it;
# contracts-check fails when either differs. Both need the pinned bin/protoc (make py-tools).

.PHONY: contracts-sync contracts-check

contracts-sync:
	@bash scripts/contracts/sync.sh

contracts-check:
	@bash scripts/contracts/sync.sh --check
