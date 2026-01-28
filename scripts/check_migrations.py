#!/usr/bin/env python3
"""Validate alembic migration chain integrity."""
import re
import sys
from pathlib import Path


def extract_revisions(filepath: Path) -> tuple[str | None, str | None]:
    """Extract revision and down_revision from a migration file."""
    content = filepath.read_text()

    revision_match = re.search(r'^revision\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    down_match = re.search(r'^down_revision\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
    down_none_match = re.search(r'^down_revision\s*=\s*None', content, re.MULTILINE)

    revision = revision_match.group(1) if revision_match else None
    down_revision = None
    if down_match:
        down_revision = down_match.group(1)
    elif down_none_match:
        down_revision = None

    return revision, down_revision


def main() -> int:
    migrations_dir = Path(__file__).parent.parent / "alembic" / "versions"

    if not migrations_dir.exists():
        print(f"Migrations directory not found: {migrations_dir}")
        return 1

    migration_files = sorted(migrations_dir.glob("*.py"))
    migration_files = [f for f in migration_files if f.name != "__init__.py"]

    if not migration_files:
        print("No migration files found")
        return 0

    # Build map of all revisions
    revisions: dict[str, Path] = {}
    down_revisions: dict[str, tuple[str | None, Path]] = {}
    errors = []

    for filepath in migration_files:
        revision, down_revision = extract_revisions(filepath)

        if revision is None:
            errors.append(f"{filepath.name}: Could not extract revision ID")
            continue

        if revision in revisions:
            errors.append(f"{filepath.name}: Duplicate revision '{revision}' (also in {revisions[revision].name})")
        else:
            revisions[revision] = filepath

        down_revisions[revision] = (down_revision, filepath)

    # Check that all down_revisions point to valid revisions
    for revision, (down_revision, filepath) in down_revisions.items():
        if down_revision is not None and down_revision not in revisions:
            errors.append(
                f"{filepath.name}: down_revision '{down_revision}' does not match any revision. "
                f"Available revisions: {', '.join(sorted(revisions.keys()))}"
            )

    # Check for multiple heads (migrations with no successor)
    heads = set(revisions.keys())
    for revision, (down_revision, _) in down_revisions.items():
        if down_revision in heads:
            heads.discard(down_revision)

    if len(heads) > 1:
        head_files = [revisions[h].name for h in sorted(heads)]
        errors.append(f"Multiple migration heads detected: {', '.join(head_files)}")

    # Check for orphans (migrations whose down_revision doesn't exist, excluding the first)
    roots = [r for r, (d, _) in down_revisions.items() if d is None]
    if len(roots) > 1:
        root_files = [revisions[r].name for r in sorted(roots)]
        errors.append(f"Multiple root migrations (down_revision=None): {', '.join(root_files)}")

    if errors:
        print("Migration chain errors found:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(f"Migration chain OK: {len(revisions)} migrations verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
