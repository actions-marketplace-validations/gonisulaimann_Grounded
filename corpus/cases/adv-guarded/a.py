import sys
import os

try:
    import yaml
except ImportError:
    yaml = None

if sys.version_info >= (3, 99):
    import tomli

print(sys, os, yaml, tomli)
