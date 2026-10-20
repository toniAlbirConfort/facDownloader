Attempting to perform the InitializeDefaultDrives operation on the 'FileSystem' provider failed.
# FacDownloader

Aplicación interna para localizar y descargar facturas recibidas en buzones Outlook autorizados.

## Arranque local

1. Copia `.env.example` a `.env` y configura `CREDENTIAL_ENCRYPTION_KEY`.
2. Ejecuta `docker compose up --build`.
3. Crea el primer administrador con:

   ```bash
   docker compose exec web python manage.py createsuperuser
   ```

4. Abre `/admin/`, crea un `Mailbox` activo y configura en la misma pantalla su credencial IMAP.

La aplicación se conecta directamente al servidor de correo mediante IMAP sobre TLS. Para cada buzón se configuran servidor, puerto, usuario y contraseña desde Django Admin; la contraseña se guarda cifrada. SMTP no es necesario para buscar ni descargar facturas.

Para las facturas de Repsol, crea en `/admin/` una `ProviderCredential` con proveedor `repsol` e introduce el CIF/NIF en el campo secreto. Se puede introducir un valor por línea para probar fallbacks cifrados en orden.

Las facturas se preparan en segundo plano, pero la descarga final se realiza en el equipo del usuario: desde la búsqueda se puede elegir una carpeta local del navegador y guardar los PDFs individualmente o descargar un ZIP. El servidor conserva los archivos solo mientras están pendientes de entrega y los elimina al completar la descarga.

## Servicios

- `web`: Django, API y UI.
- `worker`: búsquedas y descargas asíncronas.
- `portal-worker`: cola separada para automatización Playwright futura.
- `redis`: broker Celery.
- `postgres`: metadatos.

Los PDFs y las descargas ZIP usan únicamente el área temporal `download_data`; después de entregarse se eliminan junto con sus metadatos de documento.

## Desarrollo sin Docker

El proyecto puede ejecutar los tests con SQLite si las dependencias Python están instaladas:

```bash
python manage.py test
```

## Seguridad operacional

- Restringe el puerto al proxy inverso interno.
- Configura `DJANGO_SECRET_KEY`, `GRAPH_CLIENT_SECRET` y `CREDENTIAL_ENCRYPTION_KEY` como secretos.
- Limita el acceso de red del servidor al IMAP autorizado y usa contraseñas específicas del buzón cuando el proveedor las permita.
- Realiza copias de seguridad de PostgreSQL; los archivos de factura son temporales y no forman parte del respaldo.
- No intentes automatizar CAPTCHA ni 2FA.

