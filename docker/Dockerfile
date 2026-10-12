# Basis: et lille Linux-image med Python 3.12 (findes til Apple Silicon/arm64)
FROM python:3.12-slim

WORKDIR /app

# Afhaengigheder foerst, saa Docker kan genbruge dette lag ved kodeaendringer
RUN pip install --no-cache-dir aiohttp==3.10.11

# Jeres kode
COPY bully_node.py app.py ./

# Print med det samme i stedet for at buffere (ellers ser kubectl logs ingenting)
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "app.py"]
