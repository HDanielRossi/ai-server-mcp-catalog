# Publicar el repositorio en GitHub

## Opción A: GitHub CLI

```bash
cd /opt/ai/ai-server-mcp-catalog
git init
git add .
git commit -m "Initial MCP catalog"

gh auth login
gh repo create ai-server-mcp-catalog   --private   --source=.   --remote=origin   --push
```

## Opción B: repositorio creado desde la web

1. Crear un repositorio privado y vacío llamado `ai-server-mcp-catalog`.
2. No añadir README, licencia ni `.gitignore` desde GitHub.
3. Ejecutar:

```bash
cd /opt/ai/ai-server-mcp-catalog
git init
git branch -M main
git add .
git commit -m "Initial MCP catalog"
git remote add origin git@github.com:TU_USUARIO/ai-server-mcp-catalog.git
git push -u origin main
```

## Verificación antes del push

```bash
git status
git ls-files | grep -E '(^|/)\.env$' && echo "PELIGRO: .env está en Git"
```
