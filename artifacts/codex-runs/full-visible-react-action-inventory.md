# Full Visible React Action Inventory

Total rows: 156

| page_key | label | method | route | mode | location |
| --- | --- | --- | --- | --- | --- |
| dashboard | Load kit library | GET | /api/ui/kits | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Switch active kit | POST | /api/ui/kits/load | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Load existing kit | POST | /load-kit | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Create kit | POST | /api/ui/kits/create | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Open current config | POST | /view-current-kit-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Download current config | POST | /download-current-kit-config | download | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Open next step | GET | /execution | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Open Run Center | GET | /execution | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Prepare run review | POST | /prepare-execute | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Start preview run | POST | /execute-preview | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Start real run | POST | /execute | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| dashboard | Retry storage stage | POST | /retry-storage-stage | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Load global settings | GET | /api/ui/global-settings | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Save global settings | POST | /api/ui/global-settings | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Autofill IP plan | POST | /api/ui/global-settings/autofill | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Open global settings | GET | /global-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Save global settings HTML action | POST | /save-global-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Autofill IP plan HTML action | POST | /autofill-ip-plan | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Save upgrade policies | POST | /save-upgrade-policies | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| global_settings | Upload firmware media | POST | /upload-upgrade-media | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Open upgrade helper | GET | /upgrade-helper | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Save policies | POST | /save-upgrade-policies | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Upload firmware media | POST | /upload-upgrade-media | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Review Cisco upgrade plan | POST | /modules/cisco/plan-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Run Cisco upgrade | POST | /modules/cisco/run-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Read Cisco version | POST | /modules/cisco/discover-version | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Review ONTAP upgrade plan | POST | /modules/netapp/plan-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Run ONTAP upgrade | POST | /modules/netapp/run-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Plan iLO upgrade | POST | /plan-ilo-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Run iLO upgrade | POST | /run-ilo-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| upgrade_helper | Open iLO | GET | /ilo | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Load iLO state | GET | /api/ui/ilo | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Save iLO setup | POST | /api/ui/ilo/settings | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Setup iLO IP | POST | /api/ui/ilo/setup-ip | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Save iLO setup HTML action | POST | /save-ilo-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Export iLO config | POST | /export-ilo-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Read current iLO | POST | /export-ilo-inventory | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | View iLO config snapshot | POST | /view-ilo-config-snapshot | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Plan iLO firmware upgrade | POST | /plan-ilo-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Run iLO firmware upgrade | POST | /run-ilo-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | iLO upgrade activity | GET | /ilo-upgrade-activity | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ilo | Open storage setup | GET | /storage | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| esxi | Save ESXi setup | POST | /save-esxi-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| esxi | Prepare ESXi run | POST | /prepare-execute | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| esxi | Preview ESXi run | POST | /execute-preview | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| esxi | Start ESXi run | POST | /execute | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Open NetApp setup | GET | /modules/netapp | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Module status | GET | /modules/netapp/status | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Save NetApp setup | POST | /modules/netapp/save-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Test ONTAP API | POST | /modules/netapp/test-connection | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Read current ONTAP | POST | /modules/netapp/read-current-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Discover NetApp page | POST | /modules/netapp/discover-page | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Check console ports | POST | /modules/netapp/check-console-ports | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Save selected console | POST | /modules/netapp/save-console | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Read console state | POST | /modules/netapp/console-read-state | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Preview console IP commands | POST | /modules/netapp/console-cluster-mgmt-ip | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Update NetApp convention | POST | /modules/netapp/update-convention | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Setup NetApp IP | POST | /modules/netapp/apply-ip-setup | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Preview cluster IP command | POST | /modules/netapp/cluster-mgmt-ip | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Ping all NetApp IPs | POST | /modules/netapp/bootstrap-test-all | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Use discovered values | POST | /modules/netapp/use-discovered-values | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Probe ESXi and NFS | POST | /modules/netapp/probe-vmware-nfs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Discover NetApp | POST | /modules/netapp/discover | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Mark bootstrap complete | POST | /modules/netapp/bootstrap-complete | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | API readiness | POST | /modules/netapp/api-readiness | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Plan NetApp | POST | /modules/netapp/plan | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Validate NetApp | POST | /modules/netapp/validate | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Validate NetApp page | POST | /modules/netapp/validate-page | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Export NetApp plan | POST | /modules/netapp/export-plan | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Safe apply NetApp | POST | /modules/netapp/apply | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Apply NetApp page | POST | /modules/netapp/apply-page | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Check reset readiness | POST | /modules/netapp/factory-reset | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Plan ONTAP upgrade | POST | /modules/netapp/plan-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | Run ONTAP upgrade | POST | /modules/netapp/run-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| netapp | ONTAP upgrade activity | GET | /modules/netapp/upgrade-activity | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Open Cisco setup | GET | /cisco | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Check version | POST | /modules/cisco/discover-version | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Test console access | POST | /modules/cisco/discover-console | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Fix serial access | POST | /modules/cisco/fix-serial-permissions | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Setup Cisco IP | POST | /modules/cisco/bootstrap-management | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Check current config | POST | /modules/cisco/verify-console-bootstrap | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Test SSH | POST | /modules/cisco/test-ssh | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Save to config | POST | /modules/cisco/save-port-map | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Discover ports | POST | /modules/cisco/discover-ports | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Discover current state | POST | /modules/cisco/discover-state | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Preview config | POST | /modules/cisco/preview-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Apply config | POST | /modules/cisco/apply-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Approve config | POST | /modules/cisco/approve-config-plan | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Backup config | POST | /modules/cisco/backup-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Factory reset switch | POST | /modules/cisco/factory-reset | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Plan Cisco upgrade | POST | /modules/cisco/plan-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Run Cisco upgrade | POST | /modules/cisco/run-upgrade | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| cisco | Cisco upgrade activity | GET | /modules/cisco/upgrade-activity | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Load kit library | GET | /api/ui/kits | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Load existing kit | POST | /api/ui/kits/load | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Create kit | POST | /api/ui/kits/create | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Import kit config | POST | /api/ui/kits/import | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Open current kit config | GET | /api/ui/current-kit-config | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Download current kit config | GET | /api/ui/current-kit-config/download | download | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Save global settings | POST | /save-global-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Load kit | POST | /load-kit | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Create kit | POST | /new-kit | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Save kit config | POST | /save-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Autofill IP plan | POST | /autofill-ip-plan | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Upload firmware media | POST | /upload-upgrade-media | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | View current kit config | POST | /view-current-kit-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| configuration | Import kit config | POST | /import-kit-config | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Load storage state | GET | /api/ui/storage | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Save storage target | POST | /save-storage-target | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Display current storage setup | POST | /read-current-storage | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Build storage plan | POST | /plan-raid-layout | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Approve this plan | POST | /approve-storage-plan | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Apply storage layout | POST | /apply-storage-layout | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Clear invalid selections and reload inventory | POST | /repair-storage-selection | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Probe storage capabilities | POST | /probe-storage-capabilities | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Remove approval | POST | /clear-storage-approval | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Reboot storage now | POST | /reboot-storage-now | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | View storage artifact | POST | /view-storage-artifact | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Download storage artifact | POST | /download-storage-artifact | download | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Open reports | GET | /configs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| storage | Open build files | GET | /configs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Save Windows setup | POST | /save-windows-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Upload Windows image | POST | /upload-windows-image | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Plan Windows install (dry-run) | POST | /plan-windows-install | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Probe vSphere | POST | /probe-windows-vsphere | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Probe WinRM | POST | /probe-windows-winrm | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Register OVF path | POST | /register-windows-ovf-path | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| windows | Use selected template | POST | /select-windows-ovf-template | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ovf_templates | Open OVF Templates | GET | /modules/ovf-templates | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ovf_templates | Open Windows template settings | GET | /windows | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ovf_templates | Register directory | POST | /modules/ovf-templates/register-directory | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| ovf_templates | Register OVF path | POST | /register-windows-ovf-path | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| qnap | Open QNAP setup | GET | /qnap | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| qnap | Save QNAP setup | POST | /save-qnap-settings | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Open Run Center | GET | /execution | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Live job status | GET | /api/ui/job-status | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Prepare run review | POST | /prepare-execute | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Start preview run | POST | /execute-preview | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Start real run | POST | /execute | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Retry storage stage | POST | /retry-storage-stage | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Open setup page | GET | /configuration | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| execution | Open Reports | GET | /configs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Run history API | GET | /api/ui/run-history | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Search reports | GET | /configs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Open detailed history | GET | /history | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Open Reports | GET | /configs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Related reports | GET | /configs | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | View run summary | POST | /view-run-summary | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Download run summary | POST | /download-run-summary | download | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | View report | POST | /view-report | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Download report | POST | /download-report | download | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | View latest live summary | POST | /view-latest-live-summary | legacy-html | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| reports | Download debug bundle | GET | /debug-bundles/latest | download | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| technical | Technical events API | GET | /api/ui/technical-events | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| technical | Live job websocket | WS | /ws/job/{kit_name} | websocket | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
| action-map | React action catalog | GET | /api/ui/action-catalog | json | static/js/react-desktop-ui.js + app/main.py:react_ui_action_inventory |
