FROM python:3.11-slim

# CJK font so matplotlib charts render Chinese labels correctly
RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir pandas numpy matplotlib openpyxl pyarrow xlrd

COPY dahelper.py /usr/local/lib/python3.11/site-packages/dahelper.py

ENV MPLBACKEND=Agg
WORKDIR /out
CMD ["python"]
