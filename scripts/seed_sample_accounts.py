import argparse
import json

from src.container import AppContainer
from src.services.demo_accounts import replace_sample_accounts, seed_sample_accounts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replace", action="store_true", help="Replace additional local accounts with ten fresh residents; preserve configured accounts and explicitly retained usernames.")
    args = parser.parse_args()
    app = AppContainer()
    try:
        result = replace_sample_accounts(app) if args.replace else seed_sample_accounts(app)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        app.store.engine.dispose()


if __name__ == "__main__":
    main()
