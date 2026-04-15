# core-tools aliases
# Source this file from your shell aliases file, e.g.:
#   echo "source /path/to/core-tools/aliases.sh" >> ~/.bash_aliases
#
# To apply immediately:
#   source /path/to/core-tools/aliases.sh

# Resolve the directory this file lives in so aliases work regardless of
# where the repo was cloned.
_CORE_TOOLS_BIN="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/bin" 2>/dev/null && pwd)"

alias is="${_CORE_TOOLS_BIN}/interfacespec"
alias sc="${_CORE_TOOLS_BIN}/supercsv"
alias st="${_CORE_TOOLS_BIN}/supertracker"
alias email="${_CORE_TOOLS_BIN}/email-sender"

unset _CORE_TOOLS_BIN
