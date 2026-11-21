# Deploying SlideToVideo

## Read this first: the renderer cannot run on Vercel

Not a configuration problem — four hard blockers:

| What the app does | Vercel Functions |
|---|---|
| Shells out to **LibreOffice** to turn slides into images | 314 MB of binaries, installed with `apt`. The Python runtime builds from `requirements.txt`; there is no way to install system packages. |
| Shells out to **ffmpeg** for every scene and the audio master | Vendorable, but it eats the bundle budget alongside everything else. |
| A 6-slide deck took **~2m30s** to render; a 30-slide deck is far longer | 300s default, 800s max on Pro, 1800s with the extended beta. A real deck blows through it. |
| Keeps job state **in memory** and renders in **background threads**, polled over many requests | Each invocation is a fresh instance. The thread dies with the response and the next poll lands somewhere that has never heard of the job. |

Two smaller ones: uploads and the finished MP4 both exceed the 4.5 MB request
and response body limit, so they could not pass through a function anyway.

**So: the UI goes on Vercel, the renderer goes in a container.** Vercel is a
good host for the first half, and uploads go straight from the browser to the
renderer, which sidesteps the body limit entirely.

```
browser ──► Vercel (static: index.html, app.css, app.js, assets, config.js)
   │
   └──────► renderer container (Flask + LibreOffice + ffmpeg + a disk)
```

---

## Step 1 — Deploy the renderer

Anywhere that runs a container with a couple of CPUs and a disk. Fly.io below;
Cloud Run, Render and Railway all work the same way.

```bash
cd /path/to/ppt2video
cp deploy/Dockerfile deploy/.dockerignore .      # build context is the repo root
fly launch --no-deploy --name slidetovideo --copy-config --dockerfile Dockerfile
fly volumes create slidetovideo_data --size 10 --region sjc
fly deploy
fly open                                          # note the https URL
```

`deploy/fly.toml` already sets what matters:

- **one machine, no auto-stop.** Job state is in memory, so every request must
  reach the same process. If you scale this to two machines, polling breaks.
- **a volume at `/data`**, where `PPT2VIDEO_JOBS` points. Without it, renders
  vanish on restart.
- **2 performance CPUs, 4 GB.** Encoding is CPU-bound; a shared CPU crawls.

The same constraint applies elsewhere: Cloud Run needs `--min-instances=1
--max-instances=1 --cpu=2 --memory=4Gi --timeout=3600` and a mounted volume.

## Step 2 — Lock the renderer down

**The app ships with no authentication.** It was written to run on localhost.
Exposed to the internet it will accept a deck from anyone and feed it to
LibreOffice, which is a document parser with a CVE history. Do not skip this.

Pick one:

- **An identity proxy in front** — Cloudflare Access, Tailscale, an OAuth
  proxy. This is the real answer.
- **A shared token** — set `APP_TOKEN` on the renderer and put the same value
  in `config.js`. It is a speed bump, not authentication: the browser needs the
  token, so anyone who views source can read it. It stops drive-by crawling.
- **Keep it private** and run the UI locally against it.

```bash
fly secrets set APP_TOKEN="$(openssl rand -hex 24)"
fly secrets set CORS_ORIGINS="https://your-project.vercel.app"
```

`CORS_ORIGINS` is required for the split: without it the browser refuses every
cross-origin call. List exact origins, comma-separated. `*` is accepted and is
a bad idea here.

## Step 3 — Build and deploy the UI to Vercel

```bash
./deploy/build-frontend.sh https://slidetovideo.fly.dev
cd deploy/vercel
vercel deploy --prod
```

`build-frontend.sh` copies the static files and writes `config.js` with your
renderer's URL. If you set `APP_TOKEN`, add it to that file:

```js
window.SLIDETOVIDEO_API_BASE = "https://slidetovideo.fly.dev";
window.SLIDETOVIDEO_TOKEN    = "the-same-token";
```

Vercel serves `deploy/vercel/public` as a static site — no build step, no
framework, no functions. `vercel.json` caches the hashed assets for a year and
marks `config.js` `no-store` so a URL change takes effect immediately.

## Step 4 — Point them at each other and check

After the first Vercel deploy you have the real `*.vercel.app` URL. Put it in
`CORS_ORIGINS` on the renderer (step 2) and redeploy that, or the browser will
block every request.

```bash
curl -i https://slidetovideo.fly.dev/api/health \
  -H "Origin: https://your-project.vercel.app" \
  -H "X-App-Token: $APP_TOKEN"
```

Expect `200`, and an `Access-Control-Allow-Origin` echoing your Vercel origin.
Then load the Vercel URL: the chip top-right should read **all tools ready**.
If it says *server unreachable*, it is almost always CORS or the token.

---

## Where else this can run

The filter is narrow, and it is the same four requirements every time: runs a
**Dockerfile** (LibreOffice and ffmpeg come from `apt`), **keeps CPU after the
HTTP response returns** (renders run in background threads), **exactly one
instance** (job state is in memory), and a **disk**.

| Platform | Config here | Notes |
|---|---|---|
| **Fly.io** | `fly.toml` | Good default. Volume, no auto-stop, performance CPUs. |
| **Render** | `render.yaml` | Closest to zero-thought. Attaching a disk already pins it to one instance. Free tier spins down mid-render — use a paid plan. |
| **Railway** | `Dockerfile` + a volume | Usage-based. If you make the image non-root, set `RAILWAY_RUN_UID=0` or the volume is unwritable. |
| **Google Cloud Run** | `cloudrun.sh` | Works, but `--no-cpu-throttling` is mandatory: by default CPU is cut the instant you respond and the background render freezes. Pin `--min/--max-instances=1`. |
| **Plain VPS** (Hetzner, DigitalOcean, Linode) | `docker-compose.yml` | Cheapest CPU per dollar, which is what encoding actually needs. You run the TLS proxy and the updates. |
| **Azure Container Apps** | `Dockerfile` | Fine with min=max=1 and an Azure Files mount. |
| **AWS ECS Fargate** | `Dockerfile` | Works with EFS. Heaviest setup of the lot. |
| **Hugging Face Spaces** (Docker SDK) | `Dockerfile` | Surprisingly good free demo host: `apt` works, 2 vCPU. Public by default, sleeps when idle, storage is a paid add-on. |

Will **not** work, for exactly the reasons Vercel does not: Netlify Functions,
Cloudflare Workers and Pages Functions, Deno Deploy, AWS Lambda, Azure
Functions, Supabase Edge Functions. No `apt`, hard time limits, and nothing
survives the response. DigitalOcean App Platform builds the container happily
but offers no persistent disk.

The **frontend** half is not picky at all — Vercel, Netlify, Cloudflare Pages,
GitHub Pages or S3 + CloudFront all serve `deploy/vercel/public` identically,
and all are free at this size.

### What would lift the one-instance limit

The single-instance rule comes from two choices: job state in a Python dict,
and renders in background threads. Move state to Redis or Postgres and renders
to a worker queue reading from shared object storage, and the app scales
horizontally on any of the platforms above — and the serverless ones become
viable for everything except the render step itself, which still needs a
container with LibreOffice and ffmpeg. That is a real rearchitecture, worth it
only once more than one person is waiting on a render at a time.

## Simpler option: skip Vercel

Nothing requires the split. The renderer already serves the UI itself at `/`,
so deploying the container alone gives you the whole app on one URL — one
thing to deploy, no CORS, no `config.js`. The split is worth it only if you
want Vercel's CDN and preview deployments for the frontend.

## Costs and sizing

Renders are CPU-bound and long. One machine handles one render at a time
comfortably; concurrent users queue behind each other. If that matters, the
honest fix is a job queue and a pool of workers with shared storage, which is a
rearchitecture rather than a config change.

The disk holds uploaded decks, slide PNGs, audio and the finished MP4 per job —
roughly 50–100 MB per deck. Nothing prunes them; add a cron that deletes job
folders older than a day.

## What was and was not verified

The CORS split, the token gate and the static bundle were tested end to end
locally: the built frontend on one origin drove the Flask renderer on another,
cross-origin upload included. **The Dockerfile has not been built** — there was
no Docker daemon available in the environment where it was written. Expect to
iterate once on the apt package list.
