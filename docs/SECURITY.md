# Seguridad

## Tokens y credenciales

- No incluir tokens en comandos guardados en el historial cuando sea evitable.
- Guardar secretos en `.env`, con permisos `chmod 600 .env`.
- Rotar inmediatamente cualquier token publicado por accidente.
- Preferir tokens fine-grained y permisos mínimos.

## Docker

El grupo `docker` y el acceso a `/var/run/docker.sock` conceden capacidades equivalentes a root en muchos escenarios.

Antes de permitir acciones destructivas:

- pedir confirmación explícita;
- respaldar archivos Compose;
- revisar volúmenes;
- evitar eliminar imágenes o volúmenes automáticamente.

## Filesystem

No autorizar:

```text
/
/etc
/home
/root
~/.ssh
/var/run
```

Autorizar rutas concretas y separadas.

## Pruebas iniciales

Las primeras pruebas deben incluir siempre:

```text
No modifiques, elimines, reinicies ni crees recursos.
```
