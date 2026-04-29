# Filter Authoring Guide

This guide explains how to write custom TOML filters for ContextClipper to optimize command outputs.

## Anatomy of a Filter

Filters are written in TOML and evaluated by the `FilterEngine`.

```toml
[filter]
name = "example"
description = "Example filter"

[[filter.patterns]]
match_command = "^example-cmd"

[[filter.rules]]
type = "keep_matching"
pattern = "ERROR"
priority = 100
```

*More detailed documentation on rule types, priorities, and strategies will be added here.*
