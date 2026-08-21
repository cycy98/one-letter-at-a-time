#!/bin/sh
set -e
ruff check .
ruff format --check .
mypy .
python -m unittest discover -s tests -t . -q
