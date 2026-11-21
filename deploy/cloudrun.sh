#!/bin/sh
# Google Cloud Run. Works, but three flags are load-bearing:
#
#   --no-cpu-throttling   renders run in background threads AFTER the HTTP
#                         response returns. On the default request-based
#                         billing, CPU is cut the moment you respond and the
#                         render simply freezes. This is the one that bites.
#   --min/--max-instances=1   job state is in memory; a second instance would
#                         answer polls for jobs it has never heard of.
#   --timeout             only covers a single request, not the render.
set -e
PROJECT="${1:?usage: cloudrun.sh <gcp-project> [region]}"
REGION="${2:-us-central1}"
IMAGE="$REGION-docker.pkg.dev/$PROJECT/apps/slidetovideo"

gcloud builds submit --tag "$IMAGE" --project "$PROJECT" ..

gcloud run deploy slidetovideo \
  --image "$IMAGE" --project "$PROJECT" --region "$REGION" \
  --cpu 4 --memory 8Gi \
  --no-cpu-throttling \
  --min-instances 1 --max-instances 1 \
  --timeout 900 \
  --port 8808 \
  --set-env-vars PPT2VIDEO_JOBS=/data/jobs \
  --allow-unauthenticated
# Cloud Run's filesystem is in-memory and counts against RAM. For durable
# jobs mount a GCS bucket:  --add-volume=name=jobs,type=cloud-storage,bucket=YOUR_BUCKET \
#                           --add-volume-mount=volume=jobs,mount-path=/data
