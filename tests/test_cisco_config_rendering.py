import app.cisco as cisco


def _cfg():
    return {
        "hostname": "sw01",
        "username": "admin",
        "password": "Secret123!",
        "domain_name": "example.local",
        "management_vlan": 10,
        "management_ip": "192.168.1.2",
        "subnet_mask": "255.255.255.0",
        "gateway": "192.168.1.1",
        "ports": {
            "GigabitEthernet1/0/1": {"profile": "client_device"},
            "GigabitEthernet1/0/2": {"profile": "client_device"},
            "GigabitEthernet1/0/3": {"profile": "client_device", "description": "Printer"},
            "GigabitEthernet1/0/4": {"profile": "unused_blackhole"},
            "TenGigabitEthernet1/1/1": {"profile": "uplink_trunk", "description": "Uplink to Core Switch"},
        },
    }


def test_default_profile_rendering():
    rendered = cisco.render_cisco_port_config({"ports": {"GigabitEthernet1/0/1": {"profile": "client_device"}}})

    assert "interface GigabitEthernet1/0/1" in rendered
    assert "switchport mode access" in rendered
    assert "switchport access vlan 10" in rendered
    assert "spanning-tree portfast" in rendered


def test_individual_override_rendering():
    rendered = cisco.render_cisco_port_config(_cfg())

    assert "interface GigabitEthernet1/0/3" in rendered
    assert "description Printer" in rendered
    assert "interface range GigabitEthernet1/0/1, GigabitEthernet1/0/2" in rendered


def test_overlapping_range_detection():
    validation = cisco.validate_cisco_config(
        {
            "management_ip": "192.168.1.2",
            "gateway": "192.168.1.1",
            "ports": {
                "Gi1/0/1-2": {"profile": "client_device"},
                "GigabitEthernet1/0/2": {"profile": "printer"},
            },
        }
    )

    assert validation["ok"] is False
    assert "GigabitEthernet1/0/2" in validation["errors"][0]


def test_password_masking_in_baseline_rendering():
    rendered = cisco.render_cisco_baseline_config(_cfg())

    assert "Secret123!" not in rendered
    assert "username admin privilege 15 algorithm-type sha256 secret ********" in rendered


def test_full_config_includes_required_switch_run_baseline():
    cfg = _cfg()
    cfg["management_vlan"] = 80
    cfg["ntp_servers"] = ["192.168.1.1"]
    cfg["snmp"] = {
        "v3_username": "Private011",
        "v3_auth_protocol": "SHA",
        "v3_auth_password": "SnmpAuth123!",
        "v3_priv_protocol": "AES",
        "v3_priv_password": "SnmpPriv123!",
        "host": "192.168.1.50",
    }
    cfg["logging_host"] = "192.168.1.60"
    rendered = cisco.render_cisco_full_config(cfg)

    assert "lldp run" in rendered
    assert "no ip domain lookup" in rendered
    assert "username ESBAccess privilege 15 algorithm-type sha256 secret ********" in rendered
    assert "username LocalTech privilege 15 algorithm-type sha256 secret ********" in rendered
    assert "no ip http server" in rendered
    assert "transport preferred ssh" in rendered
    assert "interface vlan1" in rendered
    assert "vlan 80" in rendered
    assert "name DWAN" in rendered
    assert "interface vlan80" in rendered
    assert "ip address 192.168.1.2 255.255.255.0" in rendered
    assert "vlan 999" in rendered
    assert "name BLACK-HOLE" in rendered
    assert "interface range GigabitEthernet1/0/2 - 24" in rendered
    assert "interface range TenGigabitEthernet1/1/1 - 4" in rendered
    assert "interface range GigabitEthernet1/0/2-24" in rendered
    assert "ntp server 192.168.1.1" in rendered
    assert "logging host 192.168.1.60" in rendered
    assert "logging source-interface vlan 80" in rendered
    assert "snmp-server view Private011 iso included" in rendered
    assert "snmp-server group Private011 v3 priv write Private011" in rendered
    assert "snmp-server user Private011 Private011 v3 auth sha ******** priv aes 128 ********" in rendered
    assert "snmp-server host 192.168.1.50 informs version 3 priv Private011" in rendered
    assert "banner motd $" in rendered
    assert "banner login $" in rendered
    assert "copy run start" in rendered
    assert "SnmpAuth123!" not in rendered
    assert "SnmpPriv123!" not in rendered


def test_management_vlan_validation():
    validation = cisco.validate_cisco_config({"management_vlan": 10, "management_ip": "", "gateway": ""})

    assert validation["ok"] is False
    assert "Management SVI has no IP configured." in validation["errors"]
    assert "Management gateway is missing." in validation["errors"]


def test_discovered_interface_parsing():
    discovery = cisco.parse_cisco_discovery_outputs(
        show_interfaces_status="""
Port      Name               Status       Vlan       Duplex  Speed Type
Gi1/0/1   Client Device      connected    10         a-full  a-100 10/100/1000BaseTX
Te1/1/1   Uplink             connected    trunk      a-full  a-10G SFP-10GBase-SR
""",
        show_ip_interface_brief="""
Interface              IP-Address      OK? Method Status                Protocol
Vlan10                 192.168.1.2       YES manual up                    up
""",
        running_config_interfaces="""
interface GigabitEthernet1/0/1
 description Client Device
 no shutdown
!
interface Vlan10
 ip address 192.168.1.2 255.255.255.0
 no shutdown
""",
    )

    assert "GigabitEthernet1/0/1" in discovery["interfaces"]
    assert discovery["interfaces"]["Vlan10"]["ip_address"] == "192.168.1.2"


def test_grouping_identical_ports_into_ranges():
    rendered = cisco.render_cisco_port_config(
        {
            "ports": {
                "GigabitEthernet1/0/1": {"profile": "client_device"},
                "GigabitEthernet1/0/2": {"profile": "client_device"},
            }
        }
    )

    assert "interface range GigabitEthernet1/0/1, GigabitEthernet1/0/2" in rendered


def test_overridden_ports_stay_separate():
    rendered = cisco.render_cisco_port_config(_cfg())

    assert "interface GigabitEthernet1/0/3" in rendered
    assert "interface range GigabitEthernet1/0/1, GigabitEthernet1/0/2" in rendered
