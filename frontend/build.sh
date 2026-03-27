#!/bin/bash
set -e

echo "Building frontend..."
echo "API_BASE URL: $API_BASE"

# Use a temporary file to avoid issues with in-place editing
tmpfile=$(mktemp)

# Replace the placeholder URL with the real one from the environment variable
sed "s|http://localhost:8000|$API_BASE|g" index.html > "$tmpfile"
mv "$tmpfile" index.html

echo "Replacement complete."
cat index.html | grep "API_BASE"
