You are fixing the NetApp page in Lab Builder.

Goal:
Make the NetApp page functionally and visually similar to the improved Cisco setup page.

Problem:
The NetApp page has too many redundant, confusing, and cluttered controls.
It does not clearly guide the operator from first access to completed setup/config/upgrade readiness.

Do not work on Cisco, iLO, ESXi, OVF, QNAP, Windows, vCenter, reports, dashboard, or general global pages unless a NetApp test proves it is required.

Required NetApp page structure:
1. What this page is for
2. What to do next
3. What happened last
4. Current completion state
5. Access/setup card
6. Management IP plan card
7. Discover/verify card
8. Configure/upgrade readiness card
9. Completed state card
10. Debug Mode/details area

Operator Mode:
- Minimal and pretty.
- Show only the controls needed to complete NetApp setup/config/upgrade readiness.
- Show current/saved/discovered values separately.
- Show clear next step.
- Show last action result in the same location as Cisco.
- Hide raw detail unless Debug Mode is opened.

Debug Mode:
- Raw ONTAP/API/SSH details.
- Discovery output.
- Interface/LIF/node/controller details.
- Route/gateway details.
- Artifact links.
- Recovery suggestions.
- Any controls that are useful only for troubleshooting.

NetApp setup should guide:
1. Initial access status
2. SP/e0M/cluster/SVM management IP plan
3. Apply or verify management IPs
4. Verify SSH/API access
5. Discover controllers/nodes/interfaces/version
6. Validate readiness
7. Configure required settings
8. Upgrade readiness/upgrade action if available
9. Completed state

Use existing Lab Builder NetApp conventions as suggestions/defaults:
- Controller A SP offset .13
- Controller B SP offset .14
- cluster management .45
- Controller A e0M/node management .46
- Controller B e0M/node management .47
- SVM management .48
- iSCSI LIFs commonly .51-.54

Rules:
- Do not hard-code those IPs if kit config overrides them.
- Do not remove useful diagnostics; move them to Debug Mode.
- Remove or consolidate duplicate/redundant controls.
- Do not touch real NetApp hardware from pytest.
- Real NetApp actions must be manual/operator-triggered only.
- Add/update tests for route/template consistency, visible operator controls, Debug Mode details, and saved/discovered/current value separation.

Before editing:
- Inspect NetApp routes, services, templates, and tests.
- Identify redundant controls.
- Report the proposed simplified Operator Mode layout before editing.

After editing:
- Run focused NetApp tests.
- Run python -m pytest -q.
- Run python -m compileall app.
- Do not commit.
