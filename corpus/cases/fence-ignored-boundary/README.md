# Guide

Gate pull requests on changed lines only:

```console
grounded scan . --changed

Untracked files are fully reported, and findings off the diff still report
when their claim names a diff-touched symbol.

```console
grounded scan . --cache
```
