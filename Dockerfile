FROM python:3.9.1-buster

RUN pip install --no-cache-dir HAP-python==3.0.0 nature-remo==0.3.1

COPY naturebridge.py /NatureBridge/naturebridge.py

ENV PYTHONUNBUFFERED 1
ENV ACCESS_TOKEN <enter here>
ENV DATA_DIRECTORY=/NatureBridge/data

CMD ["python", "/NatureBridge/naturebridge.py"]
