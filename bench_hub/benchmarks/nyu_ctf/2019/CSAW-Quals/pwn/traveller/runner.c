#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <netinet/in.h>
#include <pwd.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <pty.h>

#define LISTEN_PORT 8000
#define IDLE_TIMEOUT_SECONDS 60

static void die(const char *msg) {
    perror(msg);
    exit(1);
}

static void close_quietly(int fd) {
    if (fd >= 0) {
        close(fd);
    }
}

static ssize_t write_all(int fd, const void *buf, size_t count) {
    const char *ptr = buf;
    size_t remaining = count;
    while (remaining > 0) {
        ssize_t written = write(fd, ptr, remaining);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        ptr += written;
        remaining -= (size_t)written;
    }
    return (ssize_t)count;
}

static ssize_t send_all(int fd, const void *buf, size_t count) {
    const char *ptr = buf;
    size_t remaining = count;
    while (remaining > 0) {
        ssize_t written = send(fd, ptr, remaining, 0);
        if (written < 0) {
            if (errno == EINTR) {
                continue;
            }
            return -1;
        }
        ptr += written;
        remaining -= (size_t)written;
    }
    return (ssize_t)count;
}

static void drop_to_mina_and_exec(void) {
    struct passwd *pwd = getpwnam("mina");
    if (pwd == NULL) {
        die("getpwnam");
    }

    if (initgroups(pwd->pw_name, pwd->pw_gid) != 0) {
        die("initgroups");
    }
    if (setgid(pwd->pw_gid) != 0) {
        die("setgid");
    }
    if (setuid(pwd->pw_uid) != 0) {
        die("setuid");
    }
    if (chdir("/home/mina") != 0) {
        die("chdir");
    }

    execl("/home/mina/traveller", "traveller", (char *)NULL);
    die("execl");
}

static pid_t spawn_traveller(int *master_fd) {
    pid_t pid = forkpty(master_fd, NULL, NULL, NULL);
    if (pid < 0) {
        die("forkpty");
    }
    if (pid == 0) {
        drop_to_mina_and_exec();
    }
    return pid;
}

static void kill_and_reap(pid_t child_pid) {
    int status = 0;

    if (child_pid <= 0) {
        return;
    }

    if (waitpid(child_pid, &status, WNOHANG) == child_pid) {
        return;
    }

    kill(-child_pid, SIGTERM);
    for (int i = 0; i < 10; ++i) {
        pid_t result = waitpid(child_pid, &status, WNOHANG);
        if (result == child_pid) {
            return;
        }
        usleep(100000);
    }

    kill(-child_pid, SIGKILL);
    waitpid(child_pid, &status, 0);
}

static void handle_client(int conn_fd) {
    int master_fd = -1;
    pid_t child_pid = spawn_traveller(&master_fd);
    char buf[4096];
    time_t deadline = time(NULL) + IDLE_TIMEOUT_SECONDS;

    while (1) {
        fd_set readfds;
        struct timeval timeout;
        int maxfd = conn_fd > master_fd ? conn_fd : master_fd;

        if (waitpid(child_pid, NULL, WNOHANG) == child_pid) {
            break;
        }

        timeout.tv_sec = 1;
        timeout.tv_usec = 0;

        FD_ZERO(&readfds);
        FD_SET(conn_fd, &readfds);
        FD_SET(master_fd, &readfds);

        int ready = select(maxfd + 1, &readfds, NULL, NULL, &timeout);
        if (ready < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }

        if (ready == 0) {
            if (time(NULL) >= deadline) {
                break;
            }
            continue;
        }

        if (FD_ISSET(conn_fd, &readfds)) {
            ssize_t received = recv(conn_fd, buf, sizeof(buf), 0);
            if (received <= 0 || write_all(master_fd, buf, (size_t)received) < 0) {
                break;
            }
            deadline = time(NULL) + IDLE_TIMEOUT_SECONDS;
        }

        if (FD_ISSET(master_fd, &readfds)) {
            ssize_t read_bytes = read(master_fd, buf, sizeof(buf));
            if (read_bytes <= 0 || send_all(conn_fd, buf, (size_t)read_bytes) < 0) {
                break;
            }
            deadline = time(NULL) + IDLE_TIMEOUT_SECONDS;
        }
    }

    shutdown(conn_fd, SHUT_RDWR);
    close_quietly(conn_fd);
    close_quietly(master_fd);
    kill_and_reap(child_pid);
}

static int create_listener(void) {
    int listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd < 0) {
        die("socket");
    }

    int one = 1;
    if (setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one)) != 0) {
        die("setsockopt");
    }

    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port = htons(LISTEN_PORT);

    if (bind(listen_fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        die("bind");
    }
    if (listen(listen_fd, 128) != 0) {
        die("listen");
    }

    return listen_fd;
}

int main(void) {
    int listen_fd = create_listener();

    while (1) {
        int conn_fd = accept(listen_fd, NULL, NULL);
        if (conn_fd < 0) {
            if (errno == EINTR) {
                continue;
            }
            die("accept");
        }

        while (waitpid(-1, NULL, WNOHANG) > 0) {
        }

        pid_t handler_pid = fork();
        if (handler_pid < 0) {
            close_quietly(conn_fd);
            continue;
        }

        if (handler_pid == 0) {
            close_quietly(listen_fd);
            handle_client(conn_fd);
            _exit(0);
        }

        close_quietly(conn_fd);
    }
}
