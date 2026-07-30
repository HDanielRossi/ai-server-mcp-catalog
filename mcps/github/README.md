# GitHub MCP

## Instalación

Configura primero:

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

Después:

```bash
./scripts/install-github.sh
```

## Seguridad

Usa un token fine-grained con acceso únicamente a los repositorios necesarios.
