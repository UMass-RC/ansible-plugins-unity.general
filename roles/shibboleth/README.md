# Shibboleth

Design principles:

- **general purpose**: Unlike many popular roles, this role does not pigeonhole you into learning how to use its custom templates, which surely do not encompass the full functionality of the underlying config file format. Instead, you supply your own templates.
  - In addition to fully custom templates, this role supports custom systemd unit files, and systemd states such as enabled/disabled, running/stopped, masked.
- **fault tolerant**: Backs up `/etc/shibboleth` to `/etc/shibboleth.bak.d`. If it already exists (it shouldn't), stop the role execution, manual action is required. After writing changes, run sanity check. If sanity check fails, move the failed `/etc/shibboleth` to `/etc/shibboleth.broken.d`, and restore `/etc/shibboleth.bak.d` to `/etc/shibboleth`. Don't ever restart shibd unless sanity check passes first. Error messages include output from all 3 sanity checks: initial, after changes, and after reverting backup.

This role should not interfere with web server configuration, leave that to the web server role.

`apt` package manager, systemd are required.

This role does not do copies, only templates. If you have no use for templating and the template is causing errors, consider wrapping your entire file in `{% raw %} ... {% endraw %}`.

### Usage

- write your template files
- supply the paths to your template files using the parameters below
  - you probably want to do this in `host_vars/<hostname>.yml`
- apply the role in check mode / diff mode
- apply the role for real

### Parameters

For default values and examples, see `defaults/main.yml`.

```yml
shib_systemd_enabled:
  type: bool
  notes:
    - "if true and disabled, enable. if false and enabled, disable."
shib_systemd_running:
  type: bool
  notes:
    - "if true and not started, start. if false and not stopped, stop."
    - "if false, also block restart."
shib_systemd_masked:
  type: bool
  notes:
    - "if true and not masked, mask. if false and masked, unmask."
shib_allow_restart:
  type: bool
  notes:
    - "if false, block restart"

shib_templates:
  type: list[dict[str, str]]
  notes:
    - "each dict should have exactly 5 keys: src dest owner group mode"
    - "all are passed to the `template` module"
    - "any of these can trigger a shibd service restart, if a restart is allowed"

shib_enable_supervisor:
  type: bool
  notes:
    - "if enabled, use `supervisor` to make sure that `shibauthorizer` and `shibresponder`"
    - "are always running"

shib_systemd_unit_template:
  type: str
  notes:
    - "this is used as the `src` parameter to the `template` module"
    - "where the `dest` parameter is {{ shib_systemd_unit_path }}"
    - "and the permissions are root:root:0644"
    - "if `null`, do nothing"
shib_systemd_unit_path:
  type: str
  notes:
    - "this is used as the `dest` parameter to the `template` module"
    - "where the `src` parameter is {{ shib_systemd_unit_template }}"
    - "you probably dont want to change this"
```
