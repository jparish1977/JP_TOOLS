#!/usr/bin/env bash
set -u

greet() {
  if [ "$1" = a ]; then
    echo a
  elif [ "$1" = b ]; then
    echo b
  fi
}

x=${1:-a}
if [ "$x" = a ] && [ -n "$x" ] || [ "$x" = c ]; then
  greet "$x"
fi
for i in 1 2; do
  case "$i" in
    1) echo one ;;
    2) echo two ;;
  esac
done
while false; do :; done
