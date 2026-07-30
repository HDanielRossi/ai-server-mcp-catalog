# AI Server MCP Catalog

Repositorio para documentar, instalar, verificar y mantener los servidores MCP usados en el servidor de IA.

## Entorno objetivo

- Ubuntu Server
- Claude Code
- Node.js administrado con NVM
- Docker Engine y Docker Compose
- Ollama
- Open WebUI
- ComfyUI

## MCP incluidos

| MCP | Estado | Función |
|---|---|---|
| Filesystem | Activo | Acceso limitado a archivos y directorios autorizados |
| GitHub | Activo | Repositorios, issues, pull requests y contenido remoto |
| Docker | Activo/por verificar | Contenedores, logs y estadísticas |
| SSH | Pendiente | Administración remota de otros equipos |
| ComfyUI | Pendiente | Ejecución y automatización de workflows |

## Principios de seguridad

1. No guardar tokens, claves SSH ni contraseñas en Git.
2. Usar archivos `.env` locales excluidos por `.gitignore`.
3. Autorizar únicamente las rutas necesarias.
4. Probar primero operaciones de solo lectura.
5. No dar acceso indiscriminado a `/`, `/etc`, `~/.ssh` o al socket Docker sin evaluar riesgos.
6. Revisar el repositorio del MCP antes de instalarlo.

## Inicio rápido

```bash
git clone <URL_DEL_REPOSITORIO>
cd ai-server-mcp-catalog

cp .env.example .env
nano .env

./scripts/check-prerequisites.sh
./scripts/install-filesystem.sh
./scripts/install-github.sh
./scripts/install-docker.sh
./scripts/verify-all.sh
```

## Configuración actual del servidor

Las rutas iniciales autorizadas para Filesystem MCP son:

```text
/opt/ai
/mnt/ai-storage/comfyui
/mnt/ai-storage/models
```

## Flujo de trabajo

Cada MCP tiene su propia carpeta:

```text
mcps/<nombre>/
├── README.md
└── metadata.json
```

Los scripts ejecutables están en `scripts/`.

## Secretos

El token de GitHub debe colocarse exclusivamente en `.env`:

```bash
GITHUB_PERSONAL_ACCESS_TOKEN=github_pat_xxx
```

Nunca hagas commit de `.env`.

## Verificación

```bash
claude mcp list
```

Dentro de Claude Code:

```text
/mcp
```

## Licencia

Uso privado. Puedes añadir una licencia formal más adelante.
