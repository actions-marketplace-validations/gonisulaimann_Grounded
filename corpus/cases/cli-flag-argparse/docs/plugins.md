# Writing a plugin

```python
def register(parser):
    parser.add_argument("--custom-flag")
```

```console
$ tool run --custom-flag x
```
