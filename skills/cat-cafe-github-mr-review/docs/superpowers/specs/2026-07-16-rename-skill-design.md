# Rename Skill: github-mr-review → cat-cafe-github-mr-review

**Date**: 2026-07-16
**Status**: Approved

## Context

The skill directory is already named `cat-cafe-github-mr-review`, but the `name:` field in `SKILL.md` frontmatter and internal references still use the old name `github-mr-review`. This creates a mismatch between the directory name and the declared skill name.

## Goal

Unify the skill name to `cat-cafe-github-mr-review` across all files, matching the directory name.

## Changes

4 files, 5 edits — all are string replacements of `github-mr-review` → `cat-cafe-github-mr-review`:

1. **SKILL.md** line 2: `name: github-mr-review` → `name: cat-cafe-github-mr-review`
2. **refs/reference.md** line 3: `github-mr-review/SKILL.md` → `cat-cafe-github-mr-review/SKILL.md`
3. **refs/reference.md** line 44: `github-mr-review（本 skill）` → `cat-cafe-github-mr-review（本 skill）`
4. **refs/core-knowledge.md** line 3: `github-mr-review/SKILL.md` → `cat-cafe-github-mr-review/SKILL.md`
5. **refs/phase-details.md** line 3: `github-mr-review/SKILL.md` → `cat-cafe-github-mr-review/SKILL.md`

## Out of Scope

- Directory rename (already correct)
- Description text changes
- closing-flow.md (no references to the old name)

## Verification

After edits, grep for `github-mr-review` in the skill directory should return zero matches.
