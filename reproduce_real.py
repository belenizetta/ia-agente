
import sys
import os
import shutil

# Add project root to sys.path
sys.path.append(os.getcwd())

from codebase.detect_multi import ProjectDetector
from llm.interpreter import PromptInterpreter
from planning.planner_multi import MultiServicePlanner

# 1. Setup Mock File System
base_dir = os.path.join(os.getcwd(), "temp_mocks")
if os.path.exists(base_dir):
    shutil.rmtree(base_dir)
os.makedirs(base_dir)

repos = {
    "user-service": os.path.join(base_dir, "user-service"),
    "order-service": os.path.join(base_dir, "order-service")
}

# Create dummy files
def create_file(path, content=""):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

# user-service files
create_file(os.path.join(repos["user-service"], "src/app/main.py"))
create_file(os.path.join(repos["user-service"], "src/app/models/user.py"))
create_file(os.path.join(repos["user-service"], "requirements.txt"))

# order-service files
create_file(os.path.join(repos["order-service"], "src/main.py"))
create_file(os.path.join(repos["order-service"], "src/models/order.py"))
create_file(os.path.join(repos["order-service"], "src/api/controller.py"))
create_file(os.path.join(repos["order-service"], "README.md"))
create_file(os.path.join(repos["order-service"], "migrations/env.py"))
create_file(os.path.join(repos["order-service"], "requirements.txt"))

# 2. Run ProjectDetector
print("\n--- DETECTOR ---")
detector = ProjectDetector()
project_info = detector.analyze_repos(repos)
print(f"Services detected: {list(project_info['services'].keys())}")
print(f"Endpoints (Order): {project_info['services']['order-service'].get('endpoints')}")

# 3. Run Interpreter
print("\n--- INTERPRETER ---")
prompt = "Refactoriza el endpoint de creación de órdenes. Mueve la lógica de validación de stock que está en el controlador a una función privada o servicio aparte para limpiar el código."
interpreter = PromptInterpreter()
intent_info = interpreter.interpret(prompt, project_info)
print(f"Intents: {intent_info['intents']}")
print(f"Entities: {intent_info['entities']}")
print(f"Target Service: {intent_info['target_service']}")

# 4. Run Planner
print("\n--- PLANNER ---")
planner = MultiServicePlanner()
services = planner.detect_involved_services(prompt, project_info, intent_info)
print(f"Detected Services: {services}")

if services:
    for svc in services:
        print(f"Files for {svc}:")
        files = planner.find_relevant_files(svc, project_info, intent_info, prompt)
        for f in files:
            print(f" - {f}")

# Cleanup
# shutil.rmtree(base_dir)
