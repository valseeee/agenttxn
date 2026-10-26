FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir '.[postgres]'
COPY scripts/network_probe.py ./network_probe.py
ENTRYPOINT ["python", "network_probe.py"]
