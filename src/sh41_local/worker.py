"""Container-local manager; no SaaS transport or host database access."""
import signal


def serve():
    while True:
        signal.pause()


if __name__ == "__main__":
    serve()
