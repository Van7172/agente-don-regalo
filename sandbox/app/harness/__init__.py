"""Harness: orquestador, especialistas, estado y políticas deterministas.

Sin reexportar `run_master` aquí: importar el paquete arrastraría al master, que
depende de `services.agent`, que a su vez importa `harness.contracts` — un ciclo.
Los call sites importan del submódulo concreto (`from app.harness.master import
run_master`).
"""
