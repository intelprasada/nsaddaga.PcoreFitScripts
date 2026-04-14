# Release Process

This document describes how to version and release **core-tools**.

## Versioning

The project follows [Semantic Versioning](https://semver.org/):

```
MAJOR.MINOR.PATCH
```

| Increment | When |
|-----------|------|
| MAJOR | Breaking / incompatible changes |
| MINOR | New tools or backwards-compatible features |
| PATCH | Bug fixes, documentation updates |

The canonical version lives in the `VERSION` file at the repository root.

## Step-by-step Release Workflow

### 1. Prepare the release branch

```bash
git checkout -b release/vX.Y.Z
```

### 2. Bump the version

```bash
echo "X.Y.Z" > VERSION
```

### 3. Update CHANGELOG.md

Move items from `[Unreleased]` to a new `[X.Y.Z] - YYYY-MM-DD` section.

### 4. Run the full test suite

```bash
make test
```

All tests must pass before tagging.

### 5. Commit and tag

```bash
git add VERSION CHANGELOG.md
git commit -m "chore: release vX.Y.Z"
git tag -a "vX.Y.Z" -m "Release vX.Y.Z"
git push origin release/vX.Y.Z --tags
```

### 6. Build the release tarball

```bash
make release
```

This invokes `release/build.sh` which packages the repository into
`release/core-tools-vX.Y.Z.tar.gz`.

### 7. Deploy

```bash
make deploy
```

This invokes `release/deploy.sh` which copies the tarball to the shared
install location configured inside that script.

### 8. Open a Pull Request

Open a PR from `release/vX.Y.Z` → `main`, get it reviewed, and merge.
