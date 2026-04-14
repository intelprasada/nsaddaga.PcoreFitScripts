VERSION := $(shell cat VERSION)
TOOLS   := tool-a tool-b

.PHONY: all test lint release clean help

## Default target: run all tests
all: test

## Run all unit and integration tests
test:
	@echo "==> Running tool-a tests..."
	python -m pytest tools/tool-a/tests/ -v
	@echo "==> Running tool-b tests..."
	prove tools/tool-b/tests/
	@echo "==> Running integration tests..."
	bash tests/test_integration.sh

## Run only tool-a unit tests
test-tool-a:
	python -m pytest tools/tool-a/tests/ -v

## Run only tool-b unit tests
test-tool-b:
	prove tools/tool-b/tests/

## Run integration tests
test-integration:
	bash tests/test_integration.sh

## Lint Python sources
lint:
	@echo "==> Linting Python..."
	python -m flake8 tools/tool-a/ lib/python/
	@echo "==> Linting shell scripts..."
	shellcheck bin/tool-a bin/tool-b lib/shell/common.sh release/build.sh release/deploy.sh tests/test_integration.sh

## Build a release tarball
release: test
	bash release/build.sh $(VERSION)

## Deploy the release to the shared install location
deploy:
	bash release/deploy.sh $(VERSION)

## Remove generated artifacts
clean:
	rm -rf dist/ build/ __pycache__ release/*.tar.gz release/*.zip

## Show this help
help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //'
