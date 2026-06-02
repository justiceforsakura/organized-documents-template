#!/bin/bash

BASE="$HOME/Documents/Organized Documents CLEAN"

color_folder() {
  local path="$1"
  local color="$2"
  osascript -e "tell application \"Finder\" to set label index of (POSIX file \"$path\" as alias) to $color" 2>/dev/null
}

echo "Coloring folders by depth level..."

find "$BASE" -mindepth 1 -maxdepth 1 -type d | while read dir; do
  color_folder "$dir" 7
done

find "$BASE" -mindepth 2 -maxdepth 2 -type d | while read dir; do
  color_folder "$dir" 6
done

find "$BASE" -mindepth 3 -maxdepth 3 -type d | while read dir; do
  color_folder "$dir" 5
done

find "$BASE" -mindepth 4 -maxdepth 4 -type d | while read dir; do
  color_folder "$dir" 4
done

echo "✓ Done! Folders colored by depth."
