# Network Requirements

The external client assumes DHCP/PXE bootstrap plus HTTP(S) retrieval of boot artifacts. HTTP avoids using TFTP for large kernel/initramfs payloads, while DHCP remains responsible for directing firmware to the initial network loader. Keep the imaging VLAN scoped by firewall/ACL policy and do not expose imaging control endpoints unnecessarily.

For multicast imaging, validate IGMP snooping/querier behavior and UDPcast's required traffic in the real switching environment during physical testing. Exact ports/addresses must follow ImageCtl's existing configuration rather than defaults invented here.
