SHELL := /bin/bash

.PHONY: check install-core verify list backup

check:
	./scripts/check-prerequisites.sh

install-core:
	./scripts/install-filesystem.sh
	./scripts/install-github.sh
	./scripts/install-docker.sh

verify:
	./scripts/verify-all.sh

list:
	claude mcp list

backup:
	./scripts/backup-claude-config.sh
