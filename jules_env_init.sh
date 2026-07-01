#!/bin/bash
uv tool install git+https://github.com/PhoPersonalOrg/uv-deps-switcher.git
uv-deps-switcher --deploy-templates
uv-deps-switcher external --checkout-dest "./EXTERNAL" --yes
uv python pin 3.10
uv venv --clear
# uv venv .venv_UV
uv lock
uv sync
# source .venv/bin/activate
