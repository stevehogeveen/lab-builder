Lab Builder Release Package
===========================

Read this before starting Lab Builder from a packaged release.

What is included
----------------
- Application source code.
- Dockerfile and docker-compose.yml.
- docker-compose.build.yml for connected source builds.
- requirements-runtime.txt for the runtime image dependency set.
- Release helper scripts under scripts/.
- VERSION, release-manifest.yml, and release-dependencies.yml.
- Optional prebuilt Docker image archive when supplied with the release bundle.

What is not included
--------------------
- Real kit files or customer configuration.
- Customer names, site names, host-specific paths, or operator-specific data.
- Passwords, credentials, keys, or environment files.
- Generated artifacts, reports, debug bundles, or SQLite runtime data.
- ISO files, firmware files, OVF/OVA, VMDK, or other customer media.

Persistent folders
------------------
Keep these folders beside the release checkout and mounted into the container:

- config/     Kit configuration and operator settings.
- artifacts/  Generated outputs, job state, history, debug bundles, and SQLite data.
- media/      Local ISO, firmware, OVF/OVA, VMDK, and related media.

Firmware and media
------------------
Firmware and customer-provided media must never be placed in the release
package or Docker image. Put those files in the mounted media/ folder after
install, or upload firmware and upgrade media from Upgrade Helper in the app.

Recommended folders:

- media/firmware/        iLO firmware, Cisco images, ONTAP images.
- media/esxi/base/       ESXi base ISO files.
- media/ovf/             OVF/OVA/VMDK source media.

Quick start with Docker Compose
-------------------------------
1. Install Docker and Docker Compose.
2. If your release includes a prebuilt image archive, load it:

   ./scripts/load_docker_image.sh dist/lab-builder-0.1.0-image.tar.gz

3. From the release directory, run:

   ./scripts/run_lab_builder.sh

4. Open:

   http://localhost:8000

Connected source build
----------------------
If the target host has internet access and no prebuilt image archive is
available, build the image from source:

   docker compose -f docker-compose.yml -f docker-compose.build.yml up --build

Self-contained release bundle
-----------------------------
For offline or controlled installs, distribute these files together:

- lab-builder-0.1.0.tar.gz
- lab-builder-0.1.0-image.tar.gz
- SHA256SUMS

The Docker image archive contains the app runtime dependencies. The release
still expects config/, artifacts/, and media/ to be mounted as persistent
folders outside the image.

Docker install helper for Ubuntu
--------------------------------
On Ubuntu hosts, this package includes a helper that installs Docker Engine
and the Compose plugin from Docker's official apt repository:

   ./scripts/install_docker_ubuntu.sh

The script requires sudo. After it finishes, log out and back in if you want
to run docker commands without sudo.

Check Docker locally:

   ./scripts/check_docker.sh

Export a prebuilt image for release:

   ./scripts/export_docker_image.sh

Health check
------------
The container exposes:

   http://localhost:8000/health

Backups
-------
Create a runtime-data backup:

   ./scripts/backup_data.sh

Restore a runtime-data backup:

   ./scripts/restore_data.sh backups/lab-builder-data-YYYYmmdd-HHMMSS.tar.gz
