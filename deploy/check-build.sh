#!/usr/bin/env bash
# GEO-37 — ingestion build-success observability. The ingest container emits structured JSON
# logs on stdout, including a terminal `build.success` (with build_id) or `build.failed` event.
# This filters the ingest service logs for those events so an operator (or a deploy step) can
# confirm the last artifact build actually completed.
#
# Usage:
#   ./deploy/check-build.sh             # show all build.success/build.failed events seen so far
#   ./deploy/check-build.sh --follow    # stream and wait for the next terminal event (exit by it)
#   ./deploy/check-build.sh --last      # print only the most recent event + set exit code by it
#
# Exit code: 0 if the most recent terminal event is build.success, 1 if build.failed/none (so it
# can gate a deploy). Requires `jq`; falls back to grep if jq is absent.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="${COMPOSE:-docker compose}"
MODE="${1:-}"

have_jq=0
command -v jq >/dev/null 2>&1 && have_jq=1

filter_jq='select(.event=="build.success" or .event=="build.failed")
           | "\(.event)\tbuild_id=\(.build_id // "?")\t\(.timestamp // .time // "")"'

case "$MODE" in
	--follow)
		# Stream live; jq prints each terminal event as it arrives.
		if [[ "$have_jq" -eq 1 ]]; then
			$COMPOSE logs -f --no-log-prefix ingest 2>/dev/null | jq -r --unbuffered "$filter_jq"
		else
			$COMPOSE logs -f --no-log-prefix ingest 2>/dev/null | grep -E 'build\.success|build\.failed'
		fi
		;;
	--last)
		if [[ "$have_jq" -eq 1 ]]; then
			last="$($COMPOSE logs --no-log-prefix ingest 2>/dev/null \
				| jq -rc 'select(.event=="build.success" or .event=="build.failed")' | tail -1)"
		else
			last="$($COMPOSE logs --no-log-prefix ingest 2>/dev/null \
				| grep -E 'build\.success|build\.failed' | tail -1)"
		fi
		if [[ -z "$last" ]]; then
			echo "no build.success/build.failed event found in ingest logs" >&2
			exit 1
		fi
		echo "$last"
		grep -q 'build\.success' <<<"$last"
		;;
	*)
		if [[ "$have_jq" -eq 1 ]]; then
			$COMPOSE logs --no-log-prefix ingest 2>/dev/null | jq -r "$filter_jq"
		else
			$COMPOSE logs --no-log-prefix ingest 2>/dev/null | grep -E 'build\.success|build\.failed'
		fi
		;;
esac
