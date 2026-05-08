FROM ubuntu:16.04

RUN apt-get update
RUN apt-get install -y \
	apt-utils \
	build-essential \
	socat

RUN useradd -ms /bin/sh mina
WORKDIR /home/mina

COPY ./flag.txt ./
COPY ./traveller ./
COPY ./run.sh ./
COPY ./runner.c ./

RUN gcc -O2 -Wall -Wextra -o /usr/local/bin/traveller-runner /home/mina/runner.c -lutil

RUN chown -R root:mina /home/mina && \
    chmod 750 /home/mina && \
    chown root:mina /home/mina/flag.txt && \
    chmod 440 /home/mina/flag.txt && \
    chmod 550 /home/mina/traveller && \
    chmod 550 /home/mina/run.sh && \
    chmod 440 /home/mina/runner.c

EXPOSE 8000

CMD ["/usr/local/bin/traveller-runner"]
