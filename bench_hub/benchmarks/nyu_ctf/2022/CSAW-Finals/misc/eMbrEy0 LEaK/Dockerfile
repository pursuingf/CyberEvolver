FROM ubuntu:22.04
ENV DEBIAN_FRONTEND noninteractive

RUN apt-get update && apt-get install -y socat python3
RUN groupadd -r ctf && useradd -r -g ctf ctf
RUN chmod 1733 /tmp /var/tmp /dev/shm

WORKDIR /home/ctf

ADD deploy/service.py .
ADD deploy/flag .
RUN chmod 500 ./service.py
RUN chown ctf:root . -R
RUN chmod 444 ./flag

COPY runner.py .
RUN chmod 755 ./runner.py

USER ctf

CMD ["python3", "/home/ctf/runner.py"]
EXPOSE 9999
