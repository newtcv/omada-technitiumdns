FROM python:3.13-slim-trixie
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*
# The application uses only the standard library. Remove installers, vendored
# dependencies and the bundled wheel that could reinstall them with ensurepip.
RUN python -m pip uninstall --yes pip setuptools wheel msgpack \
    && rm -rf /usr/local/lib/python3.13/ensurepip
WORKDIR /app
RUN groupadd --gid 10001 omadasync && useradd --uid 10001 --gid omadasync --no-create-home omadasync && mkdir /data && chown omadasync:omadasync /data
COPY omada_technitium /app/omada_technitium
USER 10001:10001
ENTRYPOINT ["python", "-m", "omada_technitium"]
CMD ["--config", "/app/config.json"]
