#!/bin/sh
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
for PY in "$ROOT/.venv/bin/python" python3.12 python3.11 python3; do
  if "$PY" -c 'import sys; sys.exit(sys.version_info < (3,10))' >/dev/null 2>&1; then
    "$PY" "$ROOT/scripts/manage.py" auto "$@"
    RESULT=$?
    if [ "$RESULT" -ne 0 ] && [ "$RESULT" -ne 130 ]; then
      printf '%s\n' '启动未完成，处理上方提示后重试。按回车关闭。'
      read -r IGNORE
    fi
    exit "$RESULT"
  fi
done
printf '%s\n' '请先安装 Python 3.10 或更高版本（推荐 3.12）。按回车关闭。'
read -r IGNORE
exit 1
