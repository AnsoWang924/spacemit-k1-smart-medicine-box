# Hardware Readiness

The software snapshot is not a wiring diagram. Confirm the exact board revision, camera module, cabinet controller and pinout from the corresponding hardware documentation before connecting power or signals.

| Subsystem | Software interface found in the project | Current verification |
| --- | --- | --- |
| Camera | K1 BSP camera JSON, GStreamer/SpaceMIT source, OV5647 and IMX219 capture paths | Not verified; no camera was attached |
| Microphone | USB audio role manager matches configured vendor/product IDs, then exposes ALSA capture | Not verified; no microphone was attached |
| Speaker | USB audio role manager resolves playback device and PipeWire sink | Not verified; no speaker was attached |
| Display | Browser kiosk uses the active Wayland session; remote UI is served by the gateway | Not verified; no display was attached and kiosk had no Wayland display |
| Medicine cabinet | backend/cabinet contains the controller API and device control implementation | Not verified; electrical wiring and actuator operation were not tested |

The default USB IDs in backend/audio/audio_roles.json identify the devices used during development. Change them to the actual microphone and speaker IDs when different hardware is used. Do not add USB serial numbers, enrolled identities or captured data to a public repository.

Camera JSON and device nodes are supplied by the K1 board support package; their presence in the operating system does not prove that a sensor is connected or producing frames. Validate each hardware path only after checking the applicable voltage levels, connector pinout and board-specific camera configuration.
