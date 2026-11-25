# Working on yianc

The [repository guide](../AGENTS.md) applies throughout this directory.
Before writing or modifying compiler or test `.an` files, read both the
[YIAN writing skill](../.agents/skills/yian-writing/SKILL.md) for shared
language conventions and the [yianc coding style](docs/coding_style.md)
for compiler-specific rules.

Before reporting completion, review the changed code against the applicable
sections of both guides. For example, check stage dependencies when moving a
module, its callers when changing a public declaration, and the reporting
path when changing a diagnostic. Fix violations introduced by the diff.
Use the root guide's local verification policy when selecting checks.
