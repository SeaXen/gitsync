# gitsync — multi-platform git repository sync, web UI
#
# Runtime needs are just Python's standard library + git. No pip, no npm,
# no build step. Runs the same `gitsync web` the native install does.
#
#   docker build -t gitsync .
#   docker run -d -p 7765:7765 -v gitsync-config:/data/config \
#              -v gitsync-share:/data/share gitsync
#   docker logs gitsync          # first run prints the access token
#
# or:  docker compose up -d
#
# Base image choice: the full `python` image is built on buildpack-deps and
# already ships git + ca-certificates, so this build needs NO apt/network at
# build time (more reliable than slim+apt). Want a smaller image and have
# build-time network? Swap in `FROM python:3.12-slim` and add:
#   RUN apt-get update && apt-get install -y --no-install-recommends \
#       git ca-certificates && rm -rf /var/lib/apt/lists/*

FROM python:3.12

# same file set install.sh copies, so `python3 -m gitsync` just works
WORKDIR /opt/gitsync
COPY bin ./bin
COPY gitsync ./gitsync
COPY README.md LICENSE docs quickinstall.sh ./

ENV PYTHONPATH=/opt/gitsync \
    GITSYNC_HOME=/opt/gitsync \
    GITSYNC_CONFIG_DIR=/data/config \
    GITSYNC_DATA_DIR=/data/share

# config + mirror hub live on a volume so they survive restarts and updates
RUN mkdir -p /data/config /data/share/mirrors

# run unprivileged; the mounted volume is owned by this same user
RUN chmod +x /opt/gitsync/gitsync/askpass.py /opt/gitsync/gitsync/glab_askpass.py \
 && useradd -r -m -u 10001 gitsync \
 && chown -R gitsync:gitsync /data /opt/gitsync /home/gitsync
USER gitsync
ENV HOME=/home/gitsync

EXPOSE 7765

# /healthz needs no auth — a clean signal for orchestrators
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7765/healthz', timeout=4).status==200 else 1)"

# Bind 0.0.0.0 so the published port is reachable; the access token is ALWAYS
# enforced, so this is safe inside the container's network namespace. To
# reach it from another machine you still publish the port and authenticate.
# Override port/bind by appending args, e.g.:
#   docker run ... gitsync:latest web --bind 0.0.0.0 --port 9000
#
# Note: the systemd timer does not run inside a container. Schedule syncs
# with a host cron (`gitsync sync`), a sidecar, or `gitsync watch`.
ENTRYPOINT ["python3", "-m", "gitsync"]
CMD ["web", "--bind", "0.0.0.0", "--port", "7765"]
