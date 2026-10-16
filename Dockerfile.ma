# Temporary opt-in upstream patch, not a custom provider/control plugin or runtime hook.
# Update/removal requires released equivalent behavior and affected qualification gates.
FROM ghcr.io/music-assistant/server:2.10.5@sha256:28023f8c0d96ca2496391f3218a6d70d7ef3ba84db846b3a3f3cd9ce7ef8aedc
USER root
COPY vendor/ma-volume/ /opt/garage-ma-patch/
COPY scripts/apply-ma-volume-patch.py /opt/garage-ma-patch/apply.py
RUN echo '1c6e4bac181215391a2bcaa83ba605b4f9cd8f0eecf461fe52e7ae7216b31f56  /opt/garage-ma-patch/manifest.json' | sha256sum -c - \
    && echo 'bc6bc0a6005495bc935ff1eaf22e48328c9b796a5f902512759d5b4973bc5b4d  /opt/garage-ma-patch/apply.py' | sha256sum -c - \
    && /app/venv/bin/python /opt/garage-ma-patch/apply.py \
    /app/venv/lib/python3.14/site-packages \
    /opt/garage-ma-patch/manifest.json /opt/garage-ma-patch/spotify-authoritative.patch
LABEL garage.ma.upstream.commit="e531b4ffcb9ebabd94e41b5611b816424fe03723" \
      garage.ma.upstream.patch.sha256="2c3530213db3fa11d84ed7240bed5c5ca4e39ea579e52e28004cc5408384d252" \
      garage.ma.upstream.pending-release="true" \
      garage.ma.runtime-qualified="false"
USER 1000:1000
