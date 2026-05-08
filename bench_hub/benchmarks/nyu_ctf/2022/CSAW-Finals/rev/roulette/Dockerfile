FROM ubuntu:18.04
ENV DEBIAN_FRONTEND noninteractive

RUN apt-get update && apt-get install -y socat openjdk-8-jre-headless default-jdk python3
RUN groupadd -r ctf && useradd -r -g ctf ctf
RUN chmod 1733 /tmp /var/tmp /dev/shm


WORKDIR /home/ctf

ADD chall.java .
RUN chmod 500 ./chall.java
RUN chown ctf:root . -R
ADD flag.txt .
RUN chmod 444 ./flag.txt
COPY runner.py .
RUN chmod 755 ./runner.py

USER ctf
CMD ["python3", "/home/ctf/runner.py"]
EXPOSE 4200
