#!/usr/bin/env bash
# Legacy shim — kept so direct invocation from older docs/muscle memory
# keeps working. The real script is scripts/_start.sh, normally driven
# by `npm start` from frontend/.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_start.sh" "$@"
