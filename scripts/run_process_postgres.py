import json
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from core.orchestrator import Orchestrator

def main():
    prompt = "Cambiar la base de datos de todo el sistema a PostgreSQL"
    repos = {
        "user-service": "https://github.com/belenizetta/user-service",
        "order-service": "https://github.com/belenizetta/order-service",
    }
    res = Orchestrator().process(prompt=prompt, repos=repos, base_branch="main")
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
