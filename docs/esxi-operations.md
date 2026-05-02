# ESXi Operations

Lab Builder boots ESXi by generating a custom ISO, serving it over HTTP, mounting that URL through iLO virtual media, setting a one-time CD/DVD boot override, and waiting for the configured ESXi management IP.

## Virtual media URL

The ISO URL is built from `LAB_BUILDER_PUBLIC_BASE_URL` when set. If it is not set, Lab Builder chooses the local source IP used to reach iLO and the port from `LAB_BUILDER_PORT`, `PORT`, or `8000`.

For real installs, use an address iLO can reach, for example:

```bash
LAB_BUILDER_PUBLIC_BASE_URL=http://192.168.1.51:8000
LAB_BUILDER_PORT=8000
```

Before mounting the ISO, Lab Builder now fetches the generated URL itself. If the URL is not being served, the ESXi run stops before touching boot order and gives the exact fix.

## Safe retry behavior

If iLO virtual media actions close the connection, Lab Builder reconnects and reads back live virtual media state before deciding whether the action failed. If old media is stuck, it tries standard eject, retry eject, and the observed iLO-compatible clear fallback: `PATCH {"Image": null, "Inserted": false}`.

## Run Center live advisory

Run Center shows the current ESXi management reachability when live checks are enabled. It also compares the last run's mounted media URL with the URL the next run will use, which catches DHCP/source-IP drift before another boot attempt.

Disable this optional live UI check with:

```bash
LAB_BUILDER_LIVE_RUN_CENTER_CHECKS=0
```

## Debug mode

Set this in the kit when the installer screen needs to remain visible:

```yaml
esxi:
  debug_no_reboot: true
```

This removes the automatic kickstart reboot so the iLO console can show installer success or failure.
