FROM python:3.9-slim-bookworm

RUN apt-get update && \
    apt-get install -y xinetd && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip3 install pycryptodome requests mmh3 -i https://pypi.tuna.tsinghua.edu.cn/simple 

RUN useradd -m user && \
    chown -R root:root /home/user

ADD app /home/user/
COPY xinetd /etc/xinetd.d/user

WORKDIR /home/user

EXPOSE 9999

CMD ["/usr/sbin/xinetd", "-dontfork"]
