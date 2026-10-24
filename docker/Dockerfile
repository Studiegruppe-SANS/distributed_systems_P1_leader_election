# Linux-image som basis
FROM python:3.12-slim

WORKDIR /app

# installeres før koden kopieres, så laget genbruges når koden ændres
RUN pip install --no-cache-dir aiohttp==3.10.11

COPY bully_node.py improved_bully_node.py app.py ./

# output skrives med det samme, så det kan ses i kubectl logs
ENV PYTHONUNBUFFERED=1

# porten app.py lytter på
EXPOSE 8080
CMD ["python", "app.py"]
