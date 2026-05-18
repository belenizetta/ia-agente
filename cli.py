#!/usr/bin/env python3
"""
CLI interactiva para el AI Code Orchestrator.

Uso:
    python cli.py
    python cli.py --server http://localhost:8000
"""

import argparse
import json
import sys
import time
import requests

SEPARATOR = "-" * 60
SECTION = "=" * 60


def print_header(text: str):
    print(f"\n{SECTION}")
    print(f"  {text}")
    print(SECTION)


def print_section(text: str):
    print(f"\n{SEPARATOR}")
    print(f"  {text}")
    print(SEPARATOR)


def collect_repos() -> dict:
    repos = {}
    print("\nIngresa los repositorios (nombre → URL). Deja el nombre en blanco para terminar.")
    while True:
        name = input("  Nombre del servicio (ej: user-service): ").strip()
        if not name:
            if not repos:
                print("  Necesitas al menos un repositorio.")
                continue
            break
        url = input(f"  URL del repositorio '{name}': ").strip()
        if url:
            repos[name] = url
    return repos


def collect_tokens(repos: dict) -> dict:
    tokens = {}
    print("\nTokens de acceso (opcional, presiona Enter para omitir cada uno):")
    for name in repos:
        token = input(f"  Token para '{name}': ").strip()
        if token:
            tokens[name] = token
    return tokens


EVENT_LABELS = {
    "job_start":            "Iniciando job...",
    "repo_clone":           "Clonando repositorio...",
    "repo_reuse":           "Reutilizando repositorio local...",
    "routing_info":         "Detectando servicios afectados...",
    "project_info":         "Analizando estructura del proyecto...",
    "intent_info":          "Interpretando el prompt...",
    "plan":                 "Generando plan de cambios...",
    "global_context_mapped":"Mapeando contexto de archivos...",
    "task_start":           "Ejecutando tarea...",
    "generated_changes":    "Generando codigo...",
    "ci_command_start":     "Corriendo tests...",
    "task_end":             "Tarea finalizada.",
    "job_end":              "Job finalizado.",
}

TERMINAL_STATUSES = {"pending_approval", "done", "ok", "error", "rejected"}

TIMEOUT_SECONDS = 600  # 10 minutos máximo


def poll_until_done(server: str, job_id: str, target_status: str = "pending_approval") -> dict:
    print()
    last_event = None
    seen_count = 0
    elapsed = 0

    while elapsed < TIMEOUT_SECONDS:
        try:
            resp = requests.get(f"{server}/jobs/{job_id}/status", timeout=10)

            if resp.status_code == 404:
                print(f"\r  Esperando que inicie el job...", end="", flush=True)
                time.sleep(2)
                elapsed += 2
                continue

            resp.raise_for_status()
            data = resp.json()
            status = data.get("status", "processing")
            events = data.get("all_events", [])

            # Mostrar eventos nuevos
            if len(events) > seen_count:
                for ev in events[seen_count:]:
                    name = ev.get("event", "")
                    label = EVENT_LABELS.get(name, name)

                    extra = ""
                    ev_data = ev.get("data", {})
                    if name == "repo_clone":
                        extra = f" [{ev_data.get('service', '')}]"
                    elif name == "task_start":
                        extra = f" [{ev_data.get('service', '')} → {ev_data.get('action', '')}]"
                    elif name == "task_end":
                        extra = f" [{ev_data.get('service', '')} → {ev_data.get('status', '')}]"
                    elif name == "job_end" and ev_data.get("status") == "error":
                        print(f"\n\n  ERROR: {ev_data.get('message', 'Error desconocido')}")

                    print(f"\r  {label}{extra}                    ")
                    last_event = name
                seen_count = len(events)

            if status in TERMINAL_STATUSES or status == target_status:
                return data

            time.sleep(3)
            elapsed += 3

        except KeyboardInterrupt:
            print("\n  Cancelado por el usuario.")
            sys.exit(0)
        except Exception as e:
            print(f"\r  Reintentando... ({e})", end="", flush=True)
            time.sleep(3)
            elapsed += 3

    print("\n  Timeout: el job tardó demasiado.")
    sys.exit(1)


def display_plan(server: str, job_id: str):
    try:
        resp = requests.get(f"{server}/jobs/{job_id}/plan", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"\n  Error obteniendo el plan: {e}")
        return False

    print_header("PLAN DE EJECUCION PROPUESTO")
    print(f"\n  Resumen: {data.get('resumen', 'Sin resumen.')}\n")

    for i, task in enumerate(data.get("tareas", []), 1):
        print(f"  [{i}] Servicio: {task['servicio']}  |  Accion: {task['accion']}")
        if task.get("entidad"):
            print(f"       Entidad: {task['entidad']}")
        if task.get("archivos"):
            print(f"       Archivos a modificar:")
            for f in task["archivos"]:
                print(f"         - {f}")
        if task.get("pasos"):
            print(f"       Pasos:")
            for step in task["pasos"]:
                print(f"         * {step}")
        print()

    return True


def confirm_plan(server: str, job_id: str) -> bool:
    while True:
        choice = input("  Aprobar este plan? [s/n]: ").strip().lower()
        if choice in ("s", "si", "y", "yes"):
            return True
        if choice in ("n", "no"):
            return False
        print("  Ingresa 's' para aprobar o 'n' para rechazar.")


def display_results(status_data: dict):
    print_header("RESULTADO DE LA EJECUCION")
    events = status_data.get("all_events", [])

    job_end = next((e for e in reversed(events) if e.get("event") == "job_end"), None)
    if job_end:
        results = job_end.get("data", {}).get("results", [])
        for r in results:
            svc = r.get("service", "?")
            st = r.get("status", "?")
            print(f"\n  Servicio: {svc}  |  Estado: {st}")

            if r.get("changed_files"):
                print(f"  Archivos modificados:")
                for f in r["changed_files"]:
                    print(f"    - {f}")

            if r.get("branch"):
                print(f"  Rama: {r['branch']}")

            pr = r.get("pr_info", {})
            if pr.get("url"):
                print(f"  PR/MR: {pr['url']}")

            if r.get("analysis_summary"):
                print(f"  Analisis:\n{r['analysis_summary']}")
    else:
        print("  No se encontraron resultados detallados.")


def main():
    parser = argparse.ArgumentParser(description="AI Code Orchestrator CLI")
    parser.add_argument("--server", default="http://localhost:8000", help="URL del servidor FastAPI")
    args = parser.parse_args()
    server = args.server.rstrip("/")

    print_header("AI CODE ORCHESTRATOR")
    print(f"  Servidor: {server}")

    # Verificar conexion
    try:
        requests.get(f"{server}/docs", timeout=5)
    except Exception:
        print(f"\n  ERROR: No se puede conectar al servidor en {server}")
        print(f"  Asegurate de que el servidor este corriendo:")
        print(f"    uvicorn app.main:app --reload")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 1. Recolectar datos del usuario
    # ------------------------------------------------------------------
    print_section("PASO 1: Describe los cambios a realizar")
    print()
    print("  Ingresa el prompt (termina con una linea que contenga solo '---'):")
    lines = []
    while True:
        line = input()
        if line.strip() == "---":
            break
        lines.append(line)
    prompt = "\n".join(lines).strip()

    if not prompt:
        print("  El prompt no puede estar vacio.")
        sys.exit(1)

    print_section("PASO 2: Repositorios")
    repos = collect_repos()

    tokens = collect_tokens(repos)

    base_branch = input("\n  Rama base (Enter para 'main'): ").strip() or "main"

    # ------------------------------------------------------------------
    # 2. Enviar a /process
    # ------------------------------------------------------------------
    print_section("PASO 3: Analizando repositorios y planificando")
    payload = {
        "prompt": prompt,
        "repos": repos,
        "tokens": tokens,
        "base_branch": base_branch,
    }

    try:
        resp = requests.post(f"{server}/process", json=payload, timeout=30)
        resp.raise_for_status()
        job_data = resp.json()
    except Exception as e:
        print(f"\n  ERROR enviando request: {e}")
        sys.exit(1)

    job_id = job_data["job_id"]
    print(f"\n  Job ID: {job_id}")

    # Esperar a que el plan este listo
    status_data = poll_until_done(server, job_id, target_status="pending_approval")

    if status_data.get("status") == "error":
        print(f"\n  ERROR en el procesamiento: {status_data}")
        sys.exit(1)

    # ------------------------------------------------------------------
    # 3. Mostrar plan y pedir aprobacion
    # ------------------------------------------------------------------
    print_section("PASO 4: Revision del plan")
    plan_ok = display_plan(server, job_id)

    if not plan_ok:
        print("  No se pudo obtener el plan.")
        sys.exit(1)

    approved = confirm_plan(server, job_id)

    # ------------------------------------------------------------------
    # 4. Confirmar o rechazar
    # ------------------------------------------------------------------
    try:
        resp = requests.post(
            f"{server}/confirm",
            json={"job_id": job_id, "approved": approved},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"\n  ERROR enviando confirmacion: {e}")
        sys.exit(1)

    if not approved:
        print("\n  Plan rechazado. No se realizaron cambios.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # 5. Esperar resultado
    # ------------------------------------------------------------------
    print_section("PASO 5: Ejecutando cambios")
    print(f"\n  Job ID: {job_id}")
    final_status = poll_until_done(server, job_id, target_status="done")

    display_results(final_status)
    print(f"\n  Podes consultar el audit completo en:")
    print(f"    GET {server}/jobs/{job_id}/status")
    print()


if __name__ == "__main__":
    main()
