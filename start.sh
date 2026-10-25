#!/usr/bin/env bash

if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi

set -euo pipefail
cd "$(dirname "$0")"

case $(uname -s) in
  Darwin) os=mac ;;
  Linux) if grep -qi microsoft /proc/version 2>/dev/null; then os=windows; else os=linux; fi ;;
  MINGW* | MSYS* | CYGWIN*) os=windows ;;
  *) os=other ;;
esac

die() {
  printf '%s\n' "$@" >&2
  exit 1
}

for_os() {
  case $os in
    mac) printf '%s' "$1" ;;
    linux) printf '%s' "$2" ;;
    windows) printf '%s' "$3" ;;
    *) printf 'macOS: %s\nLinux: %s\nWindows: %s' "$1" "$2" "$3" ;;
  esac
}

command -v docker >/dev/null 2>&1 || die "Docker не найден." "$(for_os \
  "Установите Docker Desktop: https://docs.docker.com/desktop/setup/install/mac-install/" \
  "Установите Docker Engine с плагином Compose: https://docs.docker.com/engine/install/" \
  "Установите Docker Desktop с WSL2: https://docs.docker.com/desktop/setup/install/windows-install/, включите интеграцию с WSL (https://docs.docker.com/desktop/features/wsl/) и запускайте скрипт из терминала WSL")"

compose_hint=$(for_os \
  "Compose входит в Docker Desktop — обновите Docker Desktop: https://docs.docker.com/desktop/setup/install/mac-install/" \
  "Установите или обновите плагин Compose: https://docs.docker.com/compose/install/linux/" \
  "Compose входит в Docker Desktop — обновите Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/")
compose_version=$(docker compose version --short 2>/dev/null) ||
  die "Не найден Docker Compose v2 (команда docker compose)." "$compose_hint"
IFS=. read -r major minor _ <<<"${compose_version#v}"
# env_file с required: false в docker-compose.yml понимает только Compose 2.24+
if [ "$major" -lt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -lt 24 ]; }; then
  die "Docker Compose $compose_version устарел, нужен 2.24 или новее." "$compose_hint"
fi

if ! docker_error=$(docker info 2>&1 >/dev/null); then
  die "Docker не запущен или недоступен." "$(for_os \
    "Запустите Docker Desktop (open -a Docker), дождитесь, пока он загрузится, и повторите ./start.sh" \
    "Запустите службу: sudo systemctl start docker. Если ошибка про permission denied — добавьте себя в группу docker: https://docs.docker.com/engine/install/linux-postinstall/" \
    "Запустите Docker Desktop в Windows и проверьте, что включена интеграция с WSL: https://docs.docker.com/desktop/features/wsl/")" \
    "" "Ответ Docker: $docker_error"
fi

if [ ! -f .env ]; then
  password=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
  (
    umask 077
    while IFS= read -r line || [ -n "$line" ]; do
      case $line in
        POSTGRES_PASSWORD=*) line="POSTGRES_PASSWORD=$password" ;;
      esac
      printf '%s\n' "$line"
    done <.env.example >.env
  )
  echo "Создал .env со случайным паролем базы."
fi

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
  local published
  published=$(docker ps --format '{{.Ports}}')
  case $published in *":$1->"*) return 0 ;; esac
  return 1
}

ours() {
  local mapped
  mapped=$(docker compose port app 8000 2>/dev/null || true)
  case $mapped in *":$1") return 0 ;; esac
  return 1
}

port_busy() { port_in_use "$1" && ! ours "$1"; }

port=${APP_PORT:-$(sed -n 's/^APP_PORT=//p' .env | tail -n 1)}
if [ -n "$port" ]; then
  if port_busy "$port"; then
    die "Порт $port (APP_PORT) занят другим процессом." \
      "Освободите его или поменяйте APP_PORT в .env."
  fi
else
  port=8000
  while port_busy "$port"; do
    port=$((port + 1))
    [ "$port" -le 8020 ] || die "Порты 8000–8020 заняты. Укажите свободный порт в .env: APP_PORT=…"
  done
  if [ -n "$(tail -c 1 .env)" ]; then echo >>.env; fi
  echo "APP_PORT=$port" >>.env
  if [ "$port" != 8000 ]; then
    echo "Порт 8000 занят — беру $port и запоминаю его в .env (APP_PORT)."
  fi
fi
export APP_PORT=$port

echo "Собираю и запускаю контейнеры…"
docker compose up --build -d

url="http://localhost:$port"
if command -v curl >/dev/null 2>&1; then
  printf 'Жду ответа %s/api/health ' "$url"
  attempts=0
  until curl -fsS --max-time 2 "$url/api/health" >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 60 ]; then
      echo
      die "Приложение не ответило за минуту. Логи: docker compose logs app migrate"
    fi
    printf '.'
    sleep 1
  done
  echo " ok"
else
  echo "curl не найден — не дождался ответа приложения; через несколько секунд оно будет доступно."
fi

cat <<EOF

Приложение: $url
Логи:       docker compose logs -f
Остановить: docker compose down
EOF
