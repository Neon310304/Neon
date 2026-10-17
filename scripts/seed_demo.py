from src.container import AppContainer
from src.services import seed_demo_data


def main() -> None:
    app = AppContainer()
    try:
        app.store.initialize()
        result = seed_demo_data(app, include_requests=True)
        print(
            f"Demo data ready for {result['resident_id']} "
            f"with {result['checklist_count']} checklist items."
        )
    finally:
        app.store.engine.dispose()


if __name__ == "__main__":
    main()
