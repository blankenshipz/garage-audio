-- Bookworm WirePlumber0.4 only. No hardware enumeration/reservation/logind.
-- Only transport/device containers mount this private socket. Cross-PID clients
-- are misclassified as flatpak by PipeWire0.3; the socket boundary admits them.
alsa_monitor.enabled = false
v4l2_monitor.enabled = false
libcamera_monitor.enabled = false
default_access.properties["enable-flatpak-portal"] = false
default_access.rules = {
  { matches = { { { "pipewire.access", "=", "flatpak" } } },
    default_permissions = "all" },
}
