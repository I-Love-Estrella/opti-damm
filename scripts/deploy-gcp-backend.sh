#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Deploy a new backend version to the Google Compute Engine VM.

Usage:
  scripts/deploy-gcp-backend.sh [options]

Options:
  --project PROJECT_ID     GCP project id. Defaults to active gcloud project.
  --zone ZONE              Compute Engine zone. Default: europe-west1-b
  --instance NAME          VM instance name. Default: damm-backend
  --app-dir PATH           App directory on the VM. Default: /opt/damm-app
  --service NAME           systemd service name. Default: damm-backend
  --ref GIT_REF            Git branch, tag, or commit to deploy.
                           Defaults to the current local branch, or main.
  --seed                   Run backend.factory after updating code.
  --force-seed             Run backend.factory --force after updating code.
  --skip-pip               Skip pip install -r requirements.txt.
  --dry-run                Print the remote script without executing it.
  -h, --help               Show this help message.

Examples:
  scripts/deploy-gcp-backend.sh
  scripts/deploy-gcp-backend.sh --ref main
  scripts/deploy-gcp-backend.sh --ref HEAD --skip-pip
  scripts/deploy-gcp-backend.sh --ref release-2026-05-09 --force-seed
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

current_branch() {
  if branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null); then
    if [[ "$branch" != "HEAD" ]]; then
      printf '%s\n' "$branch"
      return 0
    fi
  fi
  printf 'main\n'
}

shell_quote() {
  printf "%q" "$1"
}

require_cmd gcloud
require_cmd git

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
ZONE="europe-west1-b"
INSTANCE="damm-backend"
APP_DIR="/opt/damm-app"
SERVICE="damm-backend"
REF="$(current_branch)"
SEED_MODE="none"
SKIP_PIP="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT="${2:?Missing value for --project}"
      shift 2
      ;;
    --zone)
      ZONE="${2:?Missing value for --zone}"
      shift 2
      ;;
    --instance)
      INSTANCE="${2:?Missing value for --instance}"
      shift 2
      ;;
    --app-dir)
      APP_DIR="${2:?Missing value for --app-dir}"
      shift 2
      ;;
    --service)
      SERVICE="${2:?Missing value for --service}"
      shift 2
      ;;
    --ref)
      REF="${2:?Missing value for --ref}"
      shift 2
      ;;
    --seed)
      SEED_MODE="seed"
      shift
      ;;
    --force-seed)
      SEED_MODE="force"
      shift
      ;;
    --skip-pip)
      SKIP_PIP="true"
      shift
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PROJECT" ]]; then
  echo "No active gcloud project configured. Pass --project PROJECT_ID." >&2
  exit 1
fi

if ! git rev-parse --verify "$REF" >/dev/null 2>&1; then
  echo "Local git ref not found: $REF" >&2
  echo "Push or fetch that ref locally first, or pass a different --ref." >&2
  exit 1
fi

LOCAL_COMMIT="$(git rev-parse "$REF")"
SHORT_COMMIT="$(git rev-parse --short "$LOCAL_COMMIT")"
APP_DIR_Q="$(shell_quote "$APP_DIR")"
SERVICE_Q="$(shell_quote "$SERVICE")"
LOCAL_COMMIT_Q="$(shell_quote "$LOCAL_COMMIT")"

REMOTE_SCRIPT=$(cat <<EOF
set -euo pipefail

APP_DIR=$APP_DIR_Q
SERVICE=$SERVICE_Q
TARGET_COMMIT=$LOCAL_COMMIT_Q

if [[ ! -d "\$APP_DIR/.git" ]]; then
  echo "Remote app directory is missing or is not a git checkout: \$APP_DIR" >&2
  exit 1
fi

cd "\$APP_DIR"
echo "Deploying commit \$TARGET_COMMIT in \$APP_DIR"

git fetch --all --tags --prune

git checkout --force "\$TARGET_COMMIT"
EOF
)

if [[ "$SKIP_PIP" == "false" ]]; then
  REMOTE_SCRIPT+=$(cat <<'EOF'

if [[ ! -x .venv/bin/pip ]]; then
  echo "Virtualenv pip not found at .venv/bin/pip" >&2
  exit 1
fi

./.venv/bin/pip install -r requirements.txt
EOF
)
fi

case "$SEED_MODE" in
  seed)
    REMOTE_SCRIPT+=$'\n./.venv/bin/python -m backend.factory\n'
    ;;
  force)
    REMOTE_SCRIPT+=$'\n./.venv/bin/python -m backend.factory --force\n'
    ;;
esac

REMOTE_SCRIPT+=$(cat <<'EOF'

sudo systemctl restart "$SERVICE"
sleep 2
sudo systemctl --no-pager --full status "$SERVICE"
curl --fail --silent --show-error http://127.0.0.1:8000/health
EOF
)

if [[ "$DRY_RUN" == "true" ]]; then
  printf '%s\n' "$REMOTE_SCRIPT"
  exit 0
fi

echo "Project:   $PROJECT"
echo "Zone:      $ZONE"
echo "Instance:  $INSTANCE"
echo "App dir:   $APP_DIR"
echo "Service:   $SERVICE"
echo "Git ref:   $REF"
echo "Commit:    $LOCAL_COMMIT ($SHORT_COMMIT)"
echo

gcloud compute ssh "$INSTANCE" \
  --project "$PROJECT" \
  --zone "$ZONE" \
  --command "bash -lc $(shell_quote "$REMOTE_SCRIPT")"

echo
echo "Deployment finished: $SHORT_COMMIT"
echo "Public health check: https://api.optidamm.ink/health"
