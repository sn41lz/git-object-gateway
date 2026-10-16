# Git Object Gateway

Una herramienta open source para que agentes y tareas de CI lean solo los archivos que necesitan de un repositorio Git, sin crear un clon completo en cada trabajo.

Por ejemplo, un corrector de documentación descarga únicamente `docs/`. Todos los archivos pertenecen al mismo commit. El cliente comprueba sus hashes, reutiliza una caché y publica la carpeta solo cuando ha validado la descarga completa. También puedes consultar qué rutas cambiaron entre dos commits sin descargar el código.

## Probarlo

Con Docker y Compose instalados, desde el repositorio:

```sh
make demo
make test
make benchmark
```

La demo usa un repositorio temporal y una API HTTP real en loopback. No requiere cuenta ni claves de modelos. Tras instalar las dependencias, funciona sin conexión exterior. Resultados: `artifacts/demo/summary.json`; informe comparativo: `artifacts/benchmark/report.html`.

Sin Docker, con Python 3.12+ y Git 2.39+ en Linux/macOS:

```sh
make local
```

## Cuándo tiene sentido

Cuando muchos trabajos de lectura repiten descargas de los mismos archivos y pueden compartir una caché. La clonación parcial de Git sigue siendo una alternativa excelente, y puede ser más rápida con caché fría. El benchmark incluye esa comparación y `git archive`, junto con costes compartidos y muestras originales.

El servidor usa un repositorio completo que tú administras. No sustituye Git para editar código y no implementa el protocolo de Scion. El token permite leer todo el historial autorizado; seleccionar una carpeta no es un control de permisos. Las limitaciones, despliegue y ejemplos completos están en el [README principal](../README.md).

Licencia [MIT](../LICENSE). Puedes usarlo, modificarlo y contribuir.
