FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 omadasync && useradd --uid 10001 --gid omadasync --no-create-home omadasync && mkdir /data && chown omadasync:omadasync /data
COPY omada_technitium /app/omada_technitium
USER 10001:10001
ENTRYPOINT ["python", "-m", "omada_technitium"]
CMD ["--config", "/app/config.json"]
