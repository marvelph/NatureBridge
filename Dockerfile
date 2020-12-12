FROM python

COPY requirements.txt /NatureBridge/requirements.txt
WORKDIR /NatureBridge
RUN pip install --no-cache-dir -U -r requirements.txt

COPY main.py /NatureBridge/main.py

ENV PYTHONUNBUFFERED 1
ENV ACCESS_TOKEN <enter here>
ENV DATA_DIRECTORY=/NatureBridge/data
CMD ["python", "main.py"]
