FROM python:3.6.9-slim

RUN printf 'deb http://archive.debian.org/debian buster main\n' > /etc/apt/sources.list \
    && printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99archive \
    && apt-get update && apt-get install --no-install-recommends -y socat \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd ctf \
    && useradd -r -g ctf ctf

WORKDIR /home/ctf

COPY chall.py flag.txt ./
RUN chmod 500 ./chall.py && chown ctf:root . -R && chmod 444 ./flag.txt

EXPOSE 21200
COPY runner.py .
RUN chmod 755 ./runner.py

USER ctf

CMD ["python3", "/home/ctf/runner.py"]
