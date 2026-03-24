FROM python:3.12-slim

WORKDIR /workspace

# Install dependencies
COPY app/bridge/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Make all app packages importable
ENV PYTHONPATH=/workspace/app

CMD ["python", "-m", "bridge.main"]
