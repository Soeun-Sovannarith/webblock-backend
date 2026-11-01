#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): enforce the block-template contract the editor frontend relies on.
# Block components are copied into the editor's preview, so they may import only React; CSS classes are wb- prefixed.
# Exit 2 sends the problems back to Claude.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
[ -f "$f" ] || exit 0
problems=""
case "$f" in
  */app/templates/site/components/blocks/*.tsx)
    bad=$(grep -nE "^[[:space:]]*import[[:space:]].*from[[:space:]]+['\"]" "$f" | grep -vE "from[[:space:]]+['\"]react['\"]")
    [ -n "$bad" ] && problems+="Block components may import only from \"react\" (the editor copies them into its preview):"$'\n'"$bad"$'\n'
    grep -qE '^export default function ' "$f" || problems+="Missing 'export default function <Type>' (scaffold.render_registry imports the default export)."$'\n'
    name=$(basename "$f" .tsx)
    if [ "$name" != theme ] && ! grep -q "\"$name\"" "$CLAUDE_PROJECT_DIR/app/services/catalog.py"; then
      problems+="'$name' is not a key in app/services/catalog.py CATALOG; tests/test_scaffold.py requires block files == CATALOG (see the add-puck-component skill)."$'\n'
    fi ;;
  */app/templates/site/components/blocks/blocks.css)
    bad=$(grep -noE '(^|[[:space:],>+~])\.[a-zA-Z][a-zA-Z0-9_-]*' "$f" | grep -vE '\.wb-')
    [ -n "$bad" ] && problems+="Every class in blocks.css must start with wb-:"$'\n'"$bad"$'\n' ;;
  *) exit 0 ;;
esac
[ -z "$problems" ] && exit 0
printf '%s' "$problems" >&2
exit 2
