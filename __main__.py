from . import config
from .gui import run

if __name__ == "__main__":
    config.setup_logging()
    run()
