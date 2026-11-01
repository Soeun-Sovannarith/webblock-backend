#!/bin/sh
# One HTTP/1.0 answer: read the request head, then return the probe's latest answer.
while IFS= read -r line; do
  line="$(printf '%s' "$line" | tr -d '\r')"
  [ -z "$line" ] && break
done
body="catalog-probe $(cat /tmp/probe/answer)"
printf 'HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: %s\r\nConnection: close\r\n\r\n%s\n' \
  "$(( ${#body} + 1 ))" "$body"
