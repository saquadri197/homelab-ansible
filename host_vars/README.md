# host_vars — per-host overrides

Create a file named `<hostname>.yml` here to override variables for a specific host.

## Enabling UFW default deny policy on a specific host

Only do this AFTER:
1. Running `audit_firewall.yml` and reviewing the report
2. Confirming all needed ports are listed in `ufw_allowed_tcp_ports`
3. Adding any extra ports the host needs below

```yaml
# host_vars/wekan01.yml
ufw_set_default_policy: true   # enable deny-inbound default after review
ufw_allowed_tcp_ports:
  - 22    # SSH
  - 80    # HTTP
  - 443   # HTTPS
```

## Extra ports for specific hosts

```yaml
# host_vars/wordpress.yml
ufw_allowed_tcp_ports:
  - 22
  - 80
  - 443

# host_vars/portainer01.yml
ufw_allowed_tcp_ports:
  - 22
  - 9000   # Portainer UI
  - 9001   # Portainer agent

# host_vars/t-dns.yml
ufw_allowed_tcp_ports:
  - 22
  - 53     # DNS TCP
ufw_allowed_udp_ports:
  - 53     # DNS UDP
```

## Never add these hosts here for UFW changes
proxmox_hosts (proximo, proxmoxbkup, thor, loki) — managed by Proxmox UI only.
