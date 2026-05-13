# Module: ovf_templates

## Purpose
Registers reusable local OVF/OVA template directories for VM workflows.

## Behavior
- Register the full local directory, not just the `.ovf` file.
- Validate the descriptor and all referenced sidecar files such as `.vmdk` and `.nvram`.
- Store reusable template metadata under `cfg["ovf_templates"]["templates"]`.
- Let Windows and future Ubuntu/Linux workflows select a registered template instead of owning OVF upload logic directly.
- Track source location policy:
  - `local` is ready immediately because the Lab Builder server can read it directly.
  - `netapp` is blocked until the saved NetApp VMware/NFS datastore probe is ready.
  - `esxi_datastore` is reserved for a later source type.

## Validation
Use the OVF Templates page to register a directory, then select that template from Windows and run the Windows dry-run install plan.
