# 1. Preparar la PC

Guía para dejar la PC vieja (Ubuntu, 12 GB de RAM, SSD de 140 GB + rígido de
500 GB) funcionando como servidor. Cada paso dice **qué** hacer y **por qué**.

> Convención: lo que está en bloques `así` se escribe en la terminal de la PC.
> Las líneas que empiezan con `#` son comentarios, no hace falta copiarlas.

---

## 1.1 Actualizar el sistema y dejar acceso remoto (SSH)

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y openssh-server git curl htop smartmontools
```

**Por qué SSH:** para administrar la PC desde tu notebook sin tener que usar
el teclado y el monitor de la PC. Desde otra compu en la misma red:

```bash
# En la PC servidor, averiguá su IP:
hostname -I
# Desde tu notebook:
ssh tu_usuario@192.168.0.XX
```

**IP fija:** conviene que la IP no cambie. Lo más simple es reservarla en el
router (sección "DHCP" → "reserva de direcciones", asociándola a la MAC de la
PC). Así no hay que tocar la configuración de Ubuntu.

## 1.2 Que no se suspenda

Un servidor nunca debe dormirse:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

Y en el BIOS de la PC buscá la opción **"Restore on AC power loss" → "Power On"**:
si se corta la luz, la PC vuelve a prenderse sola cuando vuelve.

## 1.3 Discos: qué va en cada uno

| Disco | Qué guarda | Por qué |
|---|---|---|
| SSD 140 GB | Ubuntu + base de datos (`/srv/meteo/db`) | La base hace muchas lecturas/escrituras chicas: el SSD es mucho más rápido |
| Rígido 500 GB | Archivo de imágenes y backups (`/mnt/archivo`) | Archivos grandes que se escriben una vez: el rígido alcanza y sobra |

Los datos de las estaciones ocupan poco: decenas de estaciones cada 5–15 min
son del orden de **1–2 GB por año** (y TimescaleDB los comprime). Lo que ocupa
es el satélite (ver docs/03-satelite.md).

### Montar el rígido de 500 GB

```bash
# 1) Ver los discos. Buscá el de ~500 GB (ej. /dev/sdb) y su partición (/dev/sdb1).
lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINT,MODEL

# 2) SOLO si el disco es nuevo o lo querés borrar: formatearlo en ext4.
#    ¡CUIDADO! Esto borra todo lo que tenga. Verificá dos veces que sea el disco correcto.
# sudo mkfs.ext4 -L archivo /dev/sdb1

# 3) Ver su UUID (identificador que no cambia aunque se reordenen los discos)
sudo blkid /dev/sdb1

# 4) Crear la carpeta y agregar el disco a /etc/fstab para que se monte solo al prender
sudo mkdir -p /mnt/archivo
echo 'UUID=EL-UUID-DEL-PASO-3  /mnt/archivo  ext4  defaults,noatime,nofail  0  2' | sudo tee -a /etc/fstab
sudo mount -a          # si no da error, quedó bien
df -h /mnt/archivo
```

`nofail` hace que, si el disco falla o se desconecta, Ubuntu igual arranque.

### Salud de los discos

Una PC vieja puede tener discos gastados. Revisalos de vez en cuando:

```bash
sudo smartctl -H /dev/sda     # SSD
sudo smartctl -H /dev/sdb     # rígido
# "PASSED" = bien. Si dice FAILED o aparecen "Reallocated sectors", cambiá el disco.
```

## 1.4 Instalar Docker

**Qué es Docker:** cada pieza del sistema (la base de datos, la ingesta, el
satélite) corre en un "contenedor": una cajita aislada con exactamente las
versiones de programas que necesita. Ventajas: no ensucia Ubuntu, se instala
igual en cualquier PC, y si mañana cambiás de máquina, copiás la carpeta y
levantás todo con un comando.

```bash
# Instalador oficial de Docker
curl -fsSL https://get.docker.com | sudo sh
# Para usar docker sin "sudo" (cerrá sesión y volvé a entrar después)
sudo usermod -aG docker $USER
# Que arranque solo al prender la PC
sudo systemctl enable --now docker
# Probar
docker run --rm hello-world
```

## 1.5 Bajar el código y configurarlo

```bash
sudo mkdir -p /srv/meteo && sudo chown $USER /srv/meteo
cd /srv/meteo
git clone https://github.com/COMOESTAELTIEMPOENTUESCUELA/Estacionesmeteorologicas.git codigo
cd codigo/servidor

# Carpetas de datos
mkdir -p /srv/meteo/db
sudo mkdir -p /mnt/archivo/archivo /mnt/archivo/backups && sudo chown -R $USER /mnt/archivo

# Configuración
cp .env.example .env
openssl rand -hex 16          # copiá lo que sale...
nano .env                     # ...y pegalo en DB_PASSWORD. Guardar: Ctrl+O, Enter, Ctrl+X
```

## 1.6 Levantar todo

```bash
docker compose up -d --build        # la primera vez tarda unos minutos (baja y construye)
docker compose ps                   # db e ingesta deben decir "running" / "healthy"
docker compose logs -f ingesta      # ver qué hace (Ctrl+C para salir, sigue corriendo)
```

En los logs tendrías que ver líneas como:

```
thingspeak bavio: 96 observaciones leídas, 96 nuevas o cambiadas
ogimet omm_87593: 30 observaciones leídas, 30 nuevas o cambiadas
```

Con `restart: unless-stopped`, si se corta la luz, al volver la PC todo
arranca solo.

## 1.7 Backups automáticos (¡no saltear!)

Los datos que junta este servidor (sobre todo el histórico de las EMAs, que
ThingSpeak y Wunderground no guardan para siempre) son **irreemplazables**.
Una PC vieja puede morir cualquier día.

```bash
chmod +x scripts/backup.sh
./scripts/backup.sh                 # probarlo a mano una vez
ls -lh /mnt/archivo/backups

# Programarlo todos los días a las 3:30:
crontab -e
# y agregar esta línea al final:
30 3 * * * /srv/meteo/codigo/servidor/scripts/backup.sh >> /mnt/archivo/backups/backup.log 2>&1
```

El backup queda en el **otro disco** (si muere el SSD, está en el rígido).
Cómo restaurarlo está explicado (y probado) al principio de `scripts/backup.sh`.
Mejor todavía: una copia fuera de la casa. La forma más simple es
[rclone](https://rclone.org/) contra Google Drive (`rclone config` y después
`rclone copy /mnt/archivo/backups drive:backups-meteo`).

## 1.8 Comandos de todos los días

```bash
cd /srv/meteo/codigo/servidor
docker compose ps                           # ¿está todo andando?
docker compose logs --tail 50 ingesta       # últimos mensajes
docker compose restart ingesta              # después de editar config/estaciones.yaml
git pull && docker compose up -d --build    # actualizar a la última versión del código

# Entrar a la base para consultar (sale con \q)
docker compose exec db psql -U meteo -d meteo
```
