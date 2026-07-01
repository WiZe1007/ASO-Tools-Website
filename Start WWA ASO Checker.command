#!/bin/zsh
set -e

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
else
  echo "Python 3 не знайдено. Встанови Python 3 або створи .venv у папці проєкту."
  read -r "?Натисни Enter, щоб закрити..."
  exit 1
fi

PORT="${PORT:-5000}"
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

URL="http://127.0.0.1:${PORT}/"
echo "Запускаю WWA ASO Checker..."
echo "Адреса сайту: ${URL}"
echo
echo "Це вікно тримає сайт запущеним. Щоб зупинити сайт, натисни Ctrl+C або закрий це вікно."
echo

(
  sleep 2
  open "$URL"
) &

"$PYTHON_BIN" -m flask --app app run --host 127.0.0.1 --port "$PORT"
